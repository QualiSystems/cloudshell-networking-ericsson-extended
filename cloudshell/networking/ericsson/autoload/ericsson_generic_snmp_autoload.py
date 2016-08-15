import time

from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER
from cloudshell.configuration.cloudshell_snmp_binding_keys import SNMP_HANDLER
from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE
import re
import os
import inject

from cloudshell.networking.operations.interfaces.autoload_operations_interface import AutoloadOperationsInterface
from cloudshell.shell.core.context_utils import get_attribute_by_name
from cloudshell.shell.core.driver_context import AutoLoadDetails
from cloudshell.snmp.quali_snmp import QualiMibTable
from cloudshell.networking.autoload.networking_autoload_resource_structure import Port, PortChannel, PowerPort, \
    Chassis, Module
from cloudshell.networking.autoload.networking_autoload_resource_attributes import NetworkingStandardRootAttributes


class EricssonGenericSNMPAutoload(AutoloadOperationsInterface):
    IF_ENTITY = "ifDescr"
    ENTITY_PHYSICAL = "entPhysicalDescr"

    def __init__(self, snmp_handler=None, logger=None, supported_os=None):
        """Basic init with injected snmp handler and logger

        :param snmp_handler:
        :param logger:
        :return:
        """

        self._snmp = snmp_handler
        self._logger = logger
        self.exclusion_list = []
        self._excluded_models = []
        self.module_list = []
        self.chassis_list = []
        self.supported_os = supported_os
        self.port_list = []
        self.power_supply_list = []
        self.relative_path = {}
        self.port_mapping = {}
        self.interface_mapping_mib = None
        self.interface_mapping_key = None
        self.interface_mapping_table = None
        self.port_exclude_pattern = r'serial|stack|engine|management|mgmt|voice|foreign'
        self.port_ethernet_vendor_type_pattern = ''
        self.vendor_type_exclusion_pattern = ''
        self.module_details_regexp = r'^(?P<module_model>.*)\s+sn:(?P<serial_number>.*)\s+rev:(?P<version>.*) mfg'
        self.load_mib_list = []
        self.resources = list()
        self.attributes = list()

    @property
    def logger(self):
        if self._logger:
            logger = self._logger
        else:
            logger = inject.instance(LOGGER)
        return logger

    @property
    def snmp(self):
        if not self._snmp:
            self._snmp = inject.instance(SNMP_HANDLER)
        return self._snmp

    def load_ericsson_mib(self):
        """Adds Ericsson mibs to the QualiSnmp mibSources
        """

        path = os.path.abspath(os.path.join(os.path.dirname(__file__), '', 'mib'))
        self.snmp.update_mib_sources(path)

    def discover(self):
        pass

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
        self.get_module_list()
        self.add_relative_paths()
        self._get_chassis_attributes(self.chassis_list)
        self._get_ports_attributes()
        self._get_module_attributes()
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

    def _is_valid_device_os(self):
        """Validate device OS using snmp
        :return: True or False
        """

        version = None
        if not self.supported_os:
            config = inject.instance('config')
            self.supported_os = config.SUPPORTED_OS
        system_description = self.snmp.get(('SNMPv2-MIB', 'sysDescr'))['sysDescr']
        res = re.search(r"({0})".format("|".join(self.supported_os)),
                        system_description,
                        flags=re.DOTALL | re.IGNORECASE)
        if res:
            version = res.group(0).strip(' \s\r\n')
        if version:
            return

        self.logger.info('Detected system description: \'{0}\''.format(system_description))

        error_message = 'Incompatible driver! Please use this driver for \'{0}\' operation system(s)'. \
            format(str(tuple(self.supported_os)))
        self.logger.error(error_message)
        raise Exception(error_message)

    def _load_snmp_tables(self):
        """ Load all Ericsson required snmp tables

        :return:
        """

        self.logger.info('Start loading MIB tables:')
        self.if_table = self.snmp.get_table('IF-MIB', self.IF_ENTITY)
        self.logger.info('{0} table loaded'.format(self.IF_ENTITY))
        self.entity_table = self._get_entity_table()
        if len(self.entity_table.keys()) < 1:
            raise Exception('Cannot load entPhysicalTable. Autoload cannot continue')
        self.logger.info('Entity table loaded')

        if self.interface_mapping_mib and self.interface_mapping_key:
            self.interface_mapping_table = self.snmp.get_table(self.interface_mapping_mib, self.interface_mapping_key)
        self.lldp_local_table = self.snmp.get_table('LLDP-MIB', 'lldpLocPortDesc')
        self.lldp_remote_table = self.snmp.get_table('LLDP-MIB', 'lldpRemTable')
        self.duplex_table = self.snmp.get_table('EtherLike-MIB', 'dot3StatsIndex')
        self.ip_v4_table = self.snmp.get_table('IP-MIB', 'ipAdEntIfIndex')
        self.ip_v6_table = self.snmp.get_table('IPV6-MIB', 'ipAdEntIfIndex')
        self.port_channel_ports = self.snmp.get_table('IEEE8023-LAG-MIB', 'dot3adAggPortAttachedAggID')

        self.logger.info('MIB Tables loaded successfully')

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
            elif temp_entity_table['entPhysicalClass'] == 'powerSupply':
                self.power_supply_list.append(index)

        self._filter_entity_table(result_dict)
        return result_dict

    def add_relative_paths(self):
        """Build dictionary of relative paths for each module and port

        :return:
        """

        port_list = list(self.port_list)
        module_list = list(self.module_list)
        for module in module_list:
            if module not in self.exclusion_list:
                self.relative_path[module] = self.get_relative_path(module) + '/' + self._get_resource_id(module)
            else:
                self.module_list.remove(module)
        for port in port_list:
            if port not in self.exclusion_list:
                self.relative_path[port] = self._get_port_relative_path(
                    self.get_relative_path(port) + '/' + self._get_resource_id(port))
            else:
                self.port_list.remove(port)

    def _get_port_relative_path(self, relative_id):
        """Gets port relative address, handle situation when relative id is already exist on the same level

        :return: relative_id
        """

        if relative_id in self.relative_path.values():
            if '/' in relative_id:
                ids = relative_id.split('/')
                ids[-1] = str(int(ids[-1]) + 1000)
                result = '/'.join(ids)
            else:
                result = str(int(relative_id.split()[-1]) + 1000)
            if relative_id in self.relative_path.values():
                result = self._get_port_relative_path(result)
        else:
            result = relative_id
        return result

    def _add_resource(self, resource):
        """Add object data to resources and attributes lists

        :param resource: object which contains all required data for certain resource
        """

        self.resources.append(resource.get_autoload_resource_details())
        self.attributes.extend(resource.get_autoload_resource_attributes())

    def get_module_list(self):
        """Set list of all modules from entity mib table for provided list of ports

        :return:
        """

        for port in self.port_list:
            modules = []
            modules.extend(self._get_module_parents(port))
            for module in modules:
                if module in self.module_list:
                    continue
                if module not in self.exclusion_list and module not in self.module_list:
                    self.module_list.append(module)
                else:
                    self._excluded_models.append(module)

    def _get_module_parents(self, resource_id):
        """Search for relative path of the certain resource's parent element

        :return: parent relative path
        """

        result = []
        parent_id = int(self.entity_table[resource_id]['entPhysicalContainedIn'])
        if parent_id > 0 and parent_id in self.entity_table:
            if re.search(r'module', self.entity_table[parent_id]['entPhysicalClass']):
                result.append(parent_id)
                result.extend(self._get_module_parents(parent_id))
            elif re.search(r'chassis', self.entity_table[parent_id]['entPhysicalClass']):
                return result
            else:
                result.extend(self._get_module_parents(parent_id))
        return result

    def _get_resource_id(self, item_id):
        """Gets resource relative id

        :return: relative id
        """

        parent_id = int(self.entity_table[item_id]['entPhysicalContainedIn'])
        if parent_id > 0 and parent_id in self.entity_table:
            if re.search(r'container|backplane', self.entity_table[parent_id]['entPhysicalClass']):
                result = self.entity_table[parent_id]['entPhysicalParentRelPos']
            elif parent_id in self._excluded_models:
                result = self._get_resource_id(parent_id)
            else:
                result = self.entity_table[item_id]['entPhysicalParentRelPos']
        else:
            result = self.entity_table[item_id]['entPhysicalParentRelPos']
        return result

    def _get_chassis_attributes(self, chassis_list):
        """Get Chassis element attributes

        :param chassis_list: list of chassis to load attributes for
        :return:
        """

        self.logger.info('Start loading Chassis')
        for chassis in chassis_list:
            chassis_id = self.relative_path[chassis]
            model = self.entity_table[chassis]['entPhysicalVendorType']
            model_match = re.search(r'chassis.*', self.entity_table[chassis]['entPhysicalVendorType'], re.IGNORECASE)
            if model_match:
                model = model_match.group()
            else:
                if ':' in model:
                    model = model.split(':')[-1]

            serial_number = ''
            backplane_dict = self.entity_table.filter_by_column('Class', 'backplane').sort_by_column('ContainedIn')
            for key, value in backplane_dict.iteritems():
                if chassis == int(value['entPhysicalContainedIn']):
                    serial_number_match = re.search('(?<=SN:)\s*\S+', self.entity_table[key]['entPhysicalDescr'],
                                                    re.IGNORECASE)
                    if serial_number_match:
                        serial_number = serial_number_match.group()
                        break

            chassis_details_map = {
                'chassis_model': model,
                'serial_number': serial_number
            }
            if chassis_details_map['chassis_model'] == '':
                chassis_details_map['chassis_model'] = self.entity_table[chassis]['entPhysicalDescr']
            relative_path = '{0}'.format(chassis_id)
            chassis_object = Chassis(relative_path=relative_path, **chassis_details_map)
            self._add_resource(chassis_object)
            self.logger.info('Added ' + self.entity_table[chassis]['entPhysicalDescr'] + ' Chass')
        self.logger.info('Finished Loading Modules')

    def _get_module_attributes(self):
        """Set attributes for all discovered modules

        :return:
        """

        self.logger.info('Start loading Modules')
        for module in self.module_list:
            module_id = self.relative_path[module]
            module_index = self._get_resource_id(module)
            module_details_map = {'module_model': '', 'version': '', 'serial_number': ''}
            model_description = re.search(self.module_details_regexp,
                                          self.entity_table[module]['entPhysicalDescr'], re.IGNORECASE)
            if model_description:
                module_details_map.update(model_description.groupdict())
            if '/' in module_id and len(module_id.split('/')) < 3:
                module_name = 'Module {0}'.format(module_index)
                model = 'Generic Module'
            else:
                module_name = 'Sub Module {0}'.format(module_index)
                model = 'Generic Sub Module'
            module_object = Module(name=module_name, model=model, relative_path=module_id, **module_details_map)
            self._add_resource(module_object)

            self.logger.info('Module {} added'.format(self.entity_table[module]['entPhysicalDescr']))
        self.logger.info('Load modules completed.')

    def _filter_power_port_list(self):
        """Get power supply relative path

        :return: string relative path
        """

        power_supply_list = list(self.power_supply_list)
        for power_port in power_supply_list:
            parent_index = int(self.entity_table[power_port]['entPhysicalContainedIn'])
            if 'powerSupply' in self.entity_table[parent_index]['entPhysicalClass']:
                if parent_index in self.power_supply_list:
                    self.power_supply_list.remove(power_port)

    def _get_power_ports(self):
        """Get attributes for power ports provided in self.power_supply_list

        :return:
        """

        self.logger.info('Load Power Ports:')
        self._filter_power_port_list()
        for port in self.power_supply_list:
            port_id = self.entity_table[port]['entPhysicalParentRelPos']
            parent_index = int(self.entity_table[port]['entPhysicalContainedIn'])
            parent_id = int(self.entity_table[parent_index]['entPhysicalParentRelPos'])
            chassis_id = self.get_relative_path(parent_index)
            relative_path = '{0}/PP{1}-{2}'.format(chassis_id, parent_id, port_id)
            port_name = 'PP{0}'.format(self.power_supply_list.index(port))
            port_details = {'port_model': '', 'description': '', 'version': '', 'serial_number': ''}
            port_description = re.search(r'^(?P<port_model>.*)\s+sn:(?P<serial_number>.*)\s+' +
                                         'rev:(?P<version>.*)\s+(?P<description>mfg.*)$',
                                         self.entity_table[port]['entPhysicalDescr'], re.IGNORECASE)
            if port_description:
                port_details.update(port_description.groupdict())
            power_port_object = PowerPort(name=port_name, relative_path=relative_path, **port_details)
            self._add_resource(power_port_object)

            self.logger.info('Added ' + self.entity_table[port]['entPhysicalName'].strip(' \t\n\r') + ' Power Port')
        self.logger.info('Load Power Ports completed.')

    def _get_port_channels(self):
        """Get all port channels and set attributes for them

        :return:
        """

        existing_ids = []
        if not self.if_table:
            return
        port_channel_dict = {index: port for index, port in self.if_table.iteritems() if
                             index not in self.port_mapping.values() and '.' not in port[self.IF_ENTITY]}
        self.logger.info('Loading Port Channels:')
        for key, value in port_channel_dict.iteritems():
            type = self.snmp.get_property('IF-MIB', 'ifType', key)
            if 'ieee8023adLag' not in type:
                continue
            interface_model = value[self.IF_ENTITY]
            if ':' in interface_model:
                match_interface_name = re.search(r'\S+\d+\s+', interface_model)
                if match_interface_name:
                    interface_model = match_interface_name.group().strip(' ')

            match_object = re.search(r'\d+$', interface_model)
            if match_object:
                interface_id = match_object.group(0)
            else:
                self.logger.error('Adding of {0} failed. Name is invalid'.format(interface_model))
                continue

            if interface_id in existing_ids:
                interface_id = interface_id + interface_id

            existing_ids.append(interface_id)

            attribute_map = {'description': self.snmp.get_property('IF-MIB', 'ifAlias', key),
                             'associated_ports': self._get_associated_ports(key)}
            attribute_map.update(self._get_ip_interface_details(key))
            port_channel = PortChannel(name=interface_model, relative_path='PC{0}'.format(interface_id),
                                       **attribute_map)
            self._add_resource(port_channel)

            self.logger.info('Added ' + interface_model + ' Port Channel')
        self.logger.info('Load Port Channels completed.')

    def _get_associated_ports(self, item_id):
        """Get all ports associated with provided port channel
        :param item_id:
        :return:
        """

        result = ''
        for key, value in self.port_channel_ports.iteritems():
            if str(item_id) in value['dot3adAggPortAttachedAggID'] \
                    and key in self.if_table.keys() \
                    and self.IF_ENTITY in self.if_table[key]:
                result += self.if_table[key][self.IF_ENTITY].replace('/', '-').replace(' ', '') + '; '
        return result.strip(' \t\n\r')

    def _get_ports_attributes(self):
        """Get resource details and attributes for every port in self.port_list

        :return:
        """

        self.logger.info('Load Ports:')
        for port in self.port_list:
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

            port_object = Port(name=interface_name.replace('/', '-').title(), relative_path=self.relative_path[port],
                               **attribute_map)
            self._add_resource(port_object)
            self.logger.info('Added ' + interface_name + ' Port')
        self.logger.info('Load port completed.')

    def get_relative_path(self, item_id):
        """Build relative path for received item

        :param item_id:
        :return:
        """

        result = ''
        if item_id not in self.chassis_list:
            parent_id = int(self.entity_table[item_id]['entPhysicalContainedIn'])
            if parent_id not in self.relative_path.keys():
                if parent_id in self.module_list:
                    result = self._get_resource_id(parent_id)
                if result != '':
                    result = self.get_relative_path(parent_id) + '/' + result
                else:
                    result = self.get_relative_path(parent_id)
            else:
                result = self.relative_path[parent_id]
        else:
            result = self.relative_path[item_id]

        return result

    def _filter_entity_table(self, raw_entity_table):
        """Filters out all elements if their parents, doesn't exist, or listed in self.exclusion_list

        :param raw_entity_table: entity table with unfiltered elements
        """

        elements = raw_entity_table.filter_by_column('ContainedIn').sort_by_column('ParentRelPos').keys()
        for element in reversed(elements):
            parent_id = int(self.entity_table[element]['entPhysicalContainedIn'])

            if parent_id not in raw_entity_table or parent_id in self.exclusion_list:
                self.exclusion_list.append(element)

    def _get_ip_interface_details(self, port_index):
        """Get IP address details for provided port

        :param port_index: port index in ifTable
        :return interface_details: detected info for provided interface dict{'IPv4 Address': '', 'IPv6 Address': ''}
        """

        interface_details = {'ipv4_address': '', 'ipv6_address': ''}

        interface_id = None
        if self.interface_mapping_table:
            for key, value in self.interface_mapping_table.iteritems():
                if self.interface_mapping_key in value:
                    if str(port_index) == value[self.interface_mapping_key]:
                        interface_id = int(key.split('.')[0])
                        break

        if not interface_id:
            return interface_details

        if self.ip_v4_table and len(self.ip_v4_table) > 1:
            for key, value in self.ip_v4_table.iteritems():
                if 'ipAdEntIfIndex' in value and int(value['ipAdEntIfIndex']) == interface_id:
                    interface_details['ipv4_address'] = key
                    break
        if self.ip_v6_table and len(self.ip_v6_table) > 1:
            for key, value in self.ip_v6_table.iteritems():
                if 'ipAdEntIfIndex' in value and int(value['ipAdEntIfIndex']) == interface_id:
                    interface_details['ipv6_address'] = key
                    break
        return interface_details

    def _get_interface_details(self, port_index):
        """Get interface attributes

        :param port_index: port index in ifTable
        :return interface_details: detected info for provided interface dict{'Auto Negotiation': '', 'Duplex': ''}
        """

        interface_details = {'duplex': 'Full', 'auto_negotiation': 'False'}
        if port_index in self.port_mapping:
            try:
                auto_negotiation = \
                    self.snmp.get(('MAU-MIB', 'ifMauAutoNegAdminStatus', self.port_mapping[port_index], 1)).values()[0]
                if 'enabled' in auto_negotiation.lower():
                    interface_details['auto_negotiation'] = 'True'
            except Exception as e:
                self.logger.error('Failed to load auto negotiation property for interface {0}'.format(e.message))
            for key, value in self.duplex_table.iteritems():
                if 'dot3StatsIndex' in value.keys() and value['dot3StatsIndex'] == str(self.port_mapping[port_index]):
                    interface_duplex = self.snmp.get_property('EtherLike-MIB', 'dot3StatsDuplexStatus', key)
                    if 'halfDuplex' in interface_duplex:
                        interface_details['duplex'] = 'Half'
        return interface_details

    def _get_device_details(self):
        """Get root element attributes

        """

        self.logger.info('Load Switch Attributes:')
        result = {'system_name': self.snmp.get_property('SNMPv2-MIB', 'sysName', 0),
                  'vendor': 'Ericsson',
                  'model': self._get_device_model(),
                  'location': self.snmp.get_property('SNMPv2-MIB', 'sysLocation', 0),
                  'contact': self.snmp.get_property('SNMPv2-MIB', 'sysContact', 0),
                  'version': ''}

        match_version = re.search(r'(Version|Ericsson)\s*(?P<software_version>(IP|SE)OS\S+)\s+',
                                  self.snmp.get_property('SNMPv2-MIB', 'sysDescr', 0), re.IGNORECASE)
        if match_version:
            result['version'] = match_version.groupdict()['software_version'].replace(',', '')

        root = NetworkingStandardRootAttributes(**result)
        self.attributes.extend(root.get_autoload_resource_attributes())
        self.logger.info('Load Switch Attributes completed.')

    def _get_adjacent(self, interface_id):
        """Get connected device interface and device name to the specified port id, using cdp or lldp protocols

        :param interface_id: port id
        :return: device's name and port connected to port id
        :rtype string
        """

        result = ''
        if result == '' and self.lldp_remote_table:
            for key, value in self.lldp_local_table.iteritems():
                interface_name = self.if_table[interface_id][self.IF_ENTITY]
                if interface_name == '':
                    break
                if 'lldpLocPortDesc' in value and interface_name in value['lldpLocPortDesc']:
                    if 'lldpRemSysName' in self.lldp_remote_table and 'lldpRemPortDesc' in self.lldp_remote_table:
                        result = '{0} through {1}'.format(self.lldp_remote_table[key]['lldpRemSysName'],
                                                          self.lldp_remote_table[key]['lldpRemPortDesc'])
        return result

    def _get_device_model(self):
        """Get device model form snmp SNMPv2 mib

        :return: device model
        :rtype: str
        """

        result = ''
        if not result or result == '':
            self.snmp.load_mib(self.load_mib_list)
            match_name = re.search(r'::(?P<model>\S+$)', self.snmp.get_property('SNMPv2-MIB', 'sysObjectID', '0'))
            if match_name:
                result = match_name.groupdict()['model']
                result = re.sub('rbn|erirouter', '', result, flags=re.IGNORECASE).capitalize()
        return result

    def _get_mapping(self, port_index, port_descr):
        """Get mapping from entPhysicalTable to ifTable.
        Build mapping based on ent_alias_mapping_table if exists else build manually based on
        entPhysicalDescr <-> ifDescr mapping.

        :return: simple mapping from entPhysicalTable index to ifTable index:
        |        {entPhysicalTable index: ifTable index, ...}
        """

        port_id = None
        try:
            ent_alias_mapping_identifier = self.snmp.get(('ENTITY-MIB', 'entAliasMappingIdentifier', port_index, 0))
            port_id = int(ent_alias_mapping_identifier['entAliasMappingIdentifier'].split('.')[-1])
        except Exception as e:
            self.logger.error(e.message)

            if_table_re = "/".join(re.findall('\d+', port_descr))
            for interface in self.if_table.values():
                if re.search(if_table_re, interface[self.IF_ENTITY]):
                    port_id = int(interface['suffix'])
                    break
        return port_id
