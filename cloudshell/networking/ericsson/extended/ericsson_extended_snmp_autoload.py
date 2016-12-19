from collections import defaultdict
import re
from cloudshell.networking.ericsson.autoload.ericsson_generic_snmp_autoload import EricssonGenericSNMPAutoload
from cloudshell.networking.ericsson.extended.ericsson_autoload_entities import EricssonPort, PFE, EricssonModule
from cloudshell.shell.core.driver_context import AutoLoadDetails

from cloudshell.snmp.quali_snmp import QualiMibTable


class EricssonExtendedSNMPAutoload(EricssonGenericSNMPAutoload):
    IF_ENTITY = "ifDescr"
    ENTITY_PHYSICAL = "entPhysicalDescr"

    def __init__(self, snmp_handler=None, logger=None, supported_os=None):
        """Basic init with injected snmp handler and logger

            :param snmp_handler:
            :param logger:
            :return:
            """
        super(EricssonExtendedSNMPAutoload, self).__init__(snmp_handler, logger, supported_os)
        self.configuration = None
        self._snmp = snmp_handler
        self._logger = logger
        self.exclusion_list = []
        self._excluded_models = []
        self.module_list = []
        self.chassis_list = []
        self.pfe_dict = defaultdict(dict)
        self.supported_os = supported_os
        self.port_list = []
        self.power_supply_list = []
        self.relative_path = {}
        self.port_mapping = {}
        self.module_by_relative_path = {}
        self.interface_mapping_mib = None
        self.interface_mapping_key = None
        self.interface_mapping_table = None
        self.port_exclude_pattern = r'serial|stack|engine|management|mgmt|voice|foreign'
        self.port_ethernet_vendor_type_pattern = ''
        self.vendor_type_exclusion_pattern = ''
        self.module_details_regexp = r'^(?P<module_model>.*)\s+[Cc]ard\s+sn:(?P<serial_number>.*)\s+rev:(?P<version>.*) mfg'
        self.load_mib_list = []
        self.resources = list()
        self.attributes = list()

    def get_autoload_details(self):
        """General entry point for autoload,
        read device structure and attributes: chassis, modules, submodules, ports, port-channels and power supplies

        :return: AutoLoadDetails object
        """

        self._is_valid_device_os()
        self.logger.info('************************************************************************')
        self.logger.info('Start SNMP discovery process .....')
        self.load_ericsson_mib()
        self._get_device_details()
        if self.load_mib_list:
            self.snmp.load_mib(self.load_mib_list)
        self._load_snmp_tables()
        if len(self.chassis_list) < 1:
            self.logger.error('Entity table error, no chassis found')
            return AutoLoadDetails(list(), list())
        for chassis in self.chassis_list:
            if chassis not in self.exclusion_list:
                chassis_id = self._get_resource_id(chassis)
                if chassis_id == '-1':
                    chassis_id = '0'
                self.relative_path[chassis] = chassis_id
        self._get_chassis_attributes(self.chassis_list)
        self._get_module_attributes()
        self._get_ports_attributes()
        self._get_power_ports()
        self._get_port_channels()

        result = AutoLoadDetails(resources=self.resources, attributes=self.attributes)

        self.logger.info('*******************************************')
        self.logger.info('SNMP discovery Completed.')
        self.logger.info('The following platform structure detected:' +
                         '\nModel, Name, Relative Path, Uniqe Id')
        for resource in self.resources:
            self.logger.info('{0},\t\t{1},\t\t{2},\t\t{3}'.format(resource.model, resource.name,
                                                                  resource.relative_address,
                                                                  resource.unique_identifier))
        self.logger.info('------------------------------')
        for attribute in self.attributes:
            self.logger.info('{0},\t\t{1},\t\t{2}'.format(attribute.relative_address, attribute.attribute_name,
                                                          attribute.attribute_value))
        self.logger.info('*******************************************')

        return result

    def _get_entity_table(self):
        """Read Entity-MIB and filter out device's structure and all it's elements, like ports, modules, chassis, etc.

        :rtype: QualiMibTable
        :return: structured and filtered EntityPhysical table.
        """

        result_dict = QualiMibTable('entPhysicalTable')

        entity_table_critical_port_attr = {'entPhysicalContainedIn': 'str', 'entPhysicalClass': 'str',
                                           'entPhysicalVendorType': 'str'}
        entity_table_optional_port_attr = {'entPhysicalDescr': 'str', 'entPhysicalName': 'str'}

        physical_indexes = self.snmp.get_table('ENTITY-MIB', 'entPhysicalParentRelPos')
        for index in physical_indexes.keys():
            is_excluded = False
            if physical_indexes[index]['entPhysicalParentRelPos'] == '':
                self.exclusion_list.append(index)
                continue
            temp_entity_table = physical_indexes[index].copy()
            temp_entity_table.update(self.snmp.get_properties('ENTITY-MIB', index, entity_table_critical_port_attr)
                                     [index])
            temp_entity_table['entPhysicalVendorType'] = self.snmp.get_property('ENTITY-MIB', 'entPhysicalVendorType',
                                                                                index)
            vendor_type_oid = self.snmp.var_binds[0]._ObjectType__args[-1]._ObjectIdentity__mibNode.name
            temp_entity_table['entPhysicalVendorTypeOid'] = '.'.join(map(str, vendor_type_oid))
            if temp_entity_table['entPhysicalContainedIn'] == '':
                self.exclusion_list.append(index)
                continue

            for item in self.vendor_type_exclusion_pattern:
                if re.search(item, temp_entity_table['entPhysicalVendorType'].lower(), re.IGNORECASE):
                    is_excluded = True
                    break

            if is_excluded is True:
                continue

            temp_entity_table.update(self.snmp.get_properties('ENTITY-MIB', index, entity_table_optional_port_attr)
                                     [index])

            temp_entity_table['entPhysicalClass'] = temp_entity_table['entPhysicalClass'].replace("'", "")

            if re.search(r'stack|chassis|module|port|powerSupply|container|backplane',
                         temp_entity_table['entPhysicalClass']):
                result_dict[index] = temp_entity_table

            if temp_entity_table['entPhysicalClass'] == 'chassis':
                self.chassis_list.append(index)
            elif temp_entity_table['entPhysicalClass'] == 'port':
                if not re.search(self.port_exclude_pattern, temp_entity_table['entPhysicalName'], re.IGNORECASE) \
                        and not re.search(self.port_exclude_pattern, temp_entity_table['entPhysicalDescr'],
                                          re.IGNORECASE):
                    port_id = self._get_mapping(index, temp_entity_table[self.ENTITY_PHYSICAL])
                    if port_id and port_id in self.if_table and port_id not in self.port_mapping.values() \
                            and not re.search(self.port_exclude_pattern,
                                              self.if_table[port_id][self.IF_ENTITY], re.IGNORECASE):
                        self.port_mapping[index] = port_id
                    self.port_list.append(index)
            elif temp_entity_table['entPhysicalClass'] == 'module':
                self.module_list.append(index)
                self.pfe_dict[index] = {'pfe_0': dict()}
                pfe_configuration = self.configuration.get(temp_entity_table['entPhysicalVendorTypeOid'], None)
                if pfe_configuration:
                    temp_entity_table['entPhysicalModelName'] = str(pfe_configuration.pop('linecard_model', ''))
                    for pfe in pfe_configuration:
                        if not isinstance(pfe_configuration[pfe], dict):
                            continue
                        pfe = pfe.encode('ascii')
                        self.pfe_dict[index][pfe] = {}
                        for key, values in pfe_configuration[pfe].iteritems():
                            port_list = []
                            port_range = list(values)
                            for value in port_range:
                                if '-' in value:
                                    port_min, port_max = value.split('-')
                                    values.remove(value)
                                    port_list.extend(map(str, range(int(port_min), int(port_max) + 1)))
                                else:
                                    port_name = value.encode('ascii')
                                    values.remove(value)
                                    port_list.append(port_name)
                            port_speed = key.encode('ascii')
                            self.pfe_dict[index][pfe][port_speed] = map(str, port_list)
            elif temp_entity_table['entPhysicalClass'] == 'powerSupply':
                self.power_supply_list.append(index)

        self._filter_entity_table(result_dict)
        return result_dict

    def _get_module_info(self, description):
        module_details_map = {'module_model': '', 'version': '', 'serial_number': ''}
        model_description = re.search(self.module_details_regexp, description, re.IGNORECASE)
        if model_description:
            result = model_description.groupdict()
            module_details_map['module_model'] = result.get('module_model')
            module_details_map['version'] = result.get('version')
            module_details_map['serial_number'] = result.get('serial_number')

        return module_details_map

    def _add_resource(self, resource):
        """Add object data to resources and attributes lists

        :param resource: object which contains all required data for certain resource
        """

        self.resources.append(resource.get_autoload_resource_details())
        self.attributes.extend(resource.get_autoload_resource_attributes())

    def _get_module_attributes(self):
        """Set attributes for all discovered modules

        :return:
        """

        self.logger.info('Start loading Modules')
        for module in self.module_list:
            module_id = self.get_relative_path(module) + '/' + self._get_resource_id(module)
            self.relative_path[module] = module_id
            self.module_by_relative_path[module_id] = module
            module_index = self._get_resource_id(module)
            module_entity = self.entity_table.get(module, dict())
            ericsson_model = module_entity.get('entPhysicalModelName', '')
            module_details_map = self._get_module_info(module_entity['entPhysicalDescr'])
            if ericsson_model:
                module_details_map['ericsson_model'] = ericsson_model
            module_name = "{0} card {1}".format(module_details_map.get('module_model', ''), module_index)
            if '/' in module_id and len(module_id.split('/')) < 3:
                model = 'Generic Module'
            else:
                model = 'Generic Sub Module'
            module_object = EricssonModule(name=module_name, model=model, relative_path=module_id, **module_details_map)
            self._add_resource(module_object)
            self.logger.info('Module {} added'.format(self.entity_table[module]['entPhysicalDescr']))
            pfes = self.pfe_dict.get(module, list())
            for pfe_key in pfes:
                pfe_object = PFE(name=pfe_key.upper().replace('_', ''),
                                 relative_path="{0}/{1}".format(module_id, pfe_key.split('_')[-1]))
                self._add_resource(pfe_object)
        self.logger.info('Load modules completed.')

    def _get_ports_attributes(self):
        """Get resource details and attributes for every port in self.port_list

        :return:
        """

        self.logger.info('Load Ports:')
        for port in self.port_list:
            if port in self.exclusion_list:
                continue
            does_support_1ge = False
            does_support_10ge = False
            does_support_40ge = False
            does_support_100ge = False
            port_id = self._get_resource_id(port)
            parent_relative_path = self.get_relative_path(port)
            parent_entity_id = self.module_by_relative_path.get(parent_relative_path, '')
            if parent_entity_id:
                pfe_config = self.pfe_dict.get(parent_entity_id, list())
                for port_config in pfe_config:
                    for key, values in pfe_config[port_config].iteritems():
                        if port_id in values:
                            if '1GE' in key:
                                does_support_1ge = True
                            if '10GE' in key:
                                does_support_10ge = True
                            if '40GE' in key:
                                does_support_40ge = True
                            if '100GE' in key:
                                does_support_100ge = True
                            parent_relative_path = parent_relative_path + '/' + port_config.replace('pfe_', '')
            port_relative_path = parent_relative_path + '/' + port_id
            attribute_map = {}
            interface_name = self.entity_table[port]['entPhysicalDescr'].lower()
            if self.port_ethernet_vendor_type_pattern != '' and re.search(self.port_ethernet_vendor_type_pattern,
                                                                          self.entity_table[port][
                                                                              'entPhysicalVendorType'], re.IGNORECASE):
                interface_name = re.sub(r'.*unknown', 'ethernet', interface_name)
            match_data = re.search('.*(\d+/)+?\d+', interface_name)
            if match_data:
                interface_name = match_data.group()

            if port in self.port_mapping.keys() and self.port_mapping[port] in self.if_table:
                if_table_port_attr = {'ifType': 'str', 'ifPhysAddress': 'str', 'ifMtu': 'int', 'ifHighSpeed': 'int'}
                if_table = self.if_table[self.port_mapping[port]].copy()
                if_table.update(self.snmp.get_properties('IF-MIB', self.port_mapping[port], if_table_port_attr))
                interface_name = self.snmp.get_property('IF-MIB', 'ifName', self.port_mapping[port]).replace("'",
                                                                                                             '').lower()
                interface_type = if_table[self.port_mapping[port]]['ifType'].replace('/', '').replace("'", '')
                attribute_map = {'l2_protocol_type': interface_type,
                                 'mac': if_table[self.port_mapping[port]]['ifPhysAddress'],
                                 'mtu': if_table[self.port_mapping[port]]['ifMtu'],
                                 'supports_1ge': does_support_1ge,
                                 'supports_10ge': does_support_10ge,
                                 'supports_40ge': does_support_40ge,
                                 'supports_100ge': does_support_100ge,
                                 'bandwidth': if_table[self.port_mapping[port]]['ifHighSpeed'],
                                 'description': self.snmp.get_property('IF-MIB', 'ifAlias', self.port_mapping[port]),
                                 'adjacent': self._get_adjacent(self.port_mapping[port])}
                attribute_map.update(self._get_ip_interface_details(self.port_mapping[port]))

            attribute_map.update(self._get_interface_details(port))

            interface_name_match = re.search(r'^(?P<port>port)\s*(?P<name>\S+)\s*(?P<id>(\d+/)?\d+)', interface_name)
            if interface_name_match:
                name_dict = interface_name_match.groupdict()
                interface_name = '{0} {1} {2}'.format(name_dict['name'], name_dict['port'], name_dict['id'])

            if 'l2_protocol_type' not in attribute_map.keys():
                attribute_map['l2_protocol_type'] = ''
                if 'ethernet' in interface_name.lower():
                    attribute_map['l2_protocol_type'] = 'ethernet'
                elif 'pos' in self.entity_table[port]['entPhysicalVendorType'].lower():
                    attribute_map['l2_protocol_type'] = 'pos'

            port_object = EricssonPort(name=interface_name.replace('/', '-').title(), relative_path=port_relative_path,
                                       **attribute_map)
            self._add_resource(port_object)
            self.logger.info('Added ' + interface_name + ' Port')
        self.logger.info('Load port completed.')
