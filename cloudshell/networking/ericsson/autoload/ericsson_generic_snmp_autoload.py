from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER
from cloudshell.configuration.cloudshell_snmp_binding_keys import SNMP_HANDLER
import re
import os

import inject
from cloudshell.networking.operations.interfaces.autoload_operations_interface import AutoloadOperationsInterface

from cloudshell.shell.core.driver_context import AutoLoadDetails
from cloudshell.snmp.quali_snmp import QualiMibTable
from cloudshell.networking.autoload.networking_autoload_resource_structure import Port, PortChannel, PowerPort, \
    Chassis, Module
from cloudshell.networking.autoload.networking_autoload_resource_attributes import NetworkingStandardRootAttributes
from cloudshell.networking.cisco.resource_drivers_map import CISCO_RESOURCE_DRIVERS_MAP


class CiscoGenericSNMPAutoload(AutoloadOperationsInterface):
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
        self.entity_table_black_list = ['alarm', 'fan', 'sensor']
        self.port_exclude_pattern = r'serial|stack|engine|management|mgmt|voice|foreign'
        self.module_exclude_pattern = r'cevsfp'
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

    def load_cisco_mib(self):
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'mibs'))
        self.snmp.update_mib_sources(path)

    def discover(self):
        """General entry point for autoload,
        read device structure and attributes: chassis, modules, submodules, ports, port-channels and power supplies

        :return: AutoLoadDetails object
        """

        self._is_valid_device_os()

        self.logger.info('************************************************************************')
        self.logger.info('Start SNMP discovery process .....')

        self.load_cisco_mib()
        self._get_device_details()
        self.snmp.load_mib(['CISCO-PRODUCTS-MIB', 'CISCO-ENTITY-VENDORTYPE-OID-MIB'])
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

        self._filter_lower_bay_containers()
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
        """ Load all cisco required snmp tables

        :return:
        """

        self.logger.info('Start loading MIB tables:')
        self.if_table = self.snmp.get_table('IF-MIB', self.IF_ENTITY)
        self.logger.info('{0} table loaded'.format(self.IF_ENTITY))
        self.entity_table = self._get_entity_table()
        if len(self.entity_table.keys()) < 1:
            raise Exception('Cannot load entPhysicalTable. Autoload cannot continue')
        self.logger.info('Entity table loaded')

        self.lldp_local_table = self.snmp.get_table('LLDP-MIB', 'lldpLocPortDesc')
        self.lldp_remote_table = self.snmp.get_table('LLDP-MIB', 'lldpRemTable')
        self.cdp_index_table = self.snmp.get_table('CISCO-CDP-MIB', 'cdpInterface')
        self.cdp_table = self.snmp.get_table('CISCO-CDP-MIB', 'cdpCacheTable')
        self.duplex_table = self.snmp.get_table('EtherLike-MIB', 'dot3StatsIndex')
        self.ip_v4_table = self.snmp.get_table('IP-MIB', 'ipAddrTable')
        self.ip_v6_table = self.snmp.get_table('IPV6-MIB', 'ipv6AddrEntry')
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
                is_excluded = True
                self.exclusion_list.append(index)

            for item in self.entity_table_black_list:
                if item in temp_entity_table['entPhysicalVendorType'].lower():
                    is_excluded = True
                    break

            if is_excluded is True:
                continue

            temp_entity_table.update(self.snmp.get_properties('ENTITY-MIB', index, entity_table_optional_port_attr)
                                     [index])

            if temp_entity_table['entPhysicalClass'] == '':
                vendor_type = self.snmp.get_property('ENTITY-MIB', 'entPhysicalVendorType', index)
                index_entity_class = None
                if vendor_type == '':
                    continue
                if 'cevcontainer' in vendor_type.lower():
                    index_entity_class = 'container'
                elif 'cevchassis' in vendor_type.lower():
                    index_entity_class = 'chassis'
                elif 'cevmodule' in vendor_type.lower():
                    index_entity_class = 'module'
                elif 'cevport' in vendor_type.lower():
                    index_entity_class = 'port'
                elif 'cevpowersupply' in vendor_type.lower():
                    index_entity_class = 'powerSupply'
                if index_entity_class:
                    temp_entity_table['entPhysicalClass'] = index_entity_class
            else:
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
                    if port_id and port_id in self.if_table and port_id not in self.port_mapping.values():
                        self.port_mapping[index] = port_id
                        self.port_list.append(index)
            elif temp_entity_table['entPhysicalClass'] == 'powerSupply':
                self.power_supply_list.append(index)

        self._filter_entity_table(result_dict)
        return result_dict

    def _filter_lower_bay_containers(self):

        upper_container = None
        lower_container = None
        containers = self.entity_table.filter_by_column('Class', "container").sort_by_column('ParentRelPos').keys()
        for container in containers:
            vendor_type = self.snmp.get_property('ENTITY-MIB', 'entPhysicalVendorType', container)
            if 'uppermodulebay' in vendor_type.lower():
                upper_container = container
            if 'lowermodulebay' in vendor_type.lower():
                lower_container = container
        if lower_container and upper_container:
            child_upper_items_len = len(self.entity_table.filter_by_column('ContainedIn', str(upper_container)
                                                                           ).sort_by_column('ParentRelPos').keys())
            child_lower_items = self.entity_table.filter_by_column('ContainedIn', str(lower_container)
                                                                   ).sort_by_column('ParentRelPos').keys()
            for child in child_lower_items:
                self.entity_table[child]['entPhysicalContainedIn'] = upper_container
                self.entity_table[child]['entPhysicalParentRelPos'] = str(child_upper_items_len + int(
                    self.entity_table[child]['entPhysicalParentRelPos']))

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
                vendor_type = self.snmp.get_property('ENTITY-MIB', 'entPhysicalVendorType', module)
                if not re.search(self.module_exclude_pattern, vendor_type.lower()):
                    if module not in self.exclusion_list and module not in self.module_list:
                        self.module_list.append(module)
                else:
                    self._excluded_models.append(module)

    def _get_module_parents(self, module_id):
        result = []
        parent_id = int(self.entity_table[module_id]['entPhysicalContainedIn'])
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
            chassis_details_map = {
                'chassis_model': self.snmp.get_property('ENTITY-MIB', 'entPhysicalModelName', chassis),
                'serial_number': self.snmp.get_property('ENTITY-MIB', 'entPhysicalSerialNum', chassis)
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
            module_details_map = {
                'module_model': self.entity_table[module]['entPhysicalDescr'],
                'version': self.snmp.get_property('ENTITY-MIB', 'entPhysicalSoftwareRev', module),
                'serial_number': self.snmp.get_property('ENTITY-MIB', 'entPhysicalSerialNum', module)
            }

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
            port_details = {'port_model': self.snmp.get_property('ENTITY-MIB', 'entPhysicalModelName', port, ),
                            'description': self.snmp.get_property('ENTITY-MIB', 'entPhysicalDescr', port, 'str'),
                            'version': self.snmp.get_property('ENTITY-MIB', 'entPhysicalHardwareRev', port),
                            'serial_number': self.snmp.get_property('ENTITY-MIB', 'entPhysicalSerialNum', port)
                            }
            power_port_object = PowerPort(name=port_name, relative_path=relative_path, **port_details)
            self._add_resource(power_port_object)

            self.logger.info('Added ' + self.entity_table[port]['entPhysicalName'].strip(' \t\n\r') + ' Power Port')
        self.logger.info('Load Power Ports completed.')

    def _get_port_channels(self):
        """Get all port channels and set attributes for them

        :return:
        """

        if not self.if_table:
            return
        port_channel_dic = {index: port for index, port in self.if_table.iteritems() if
                            'channel' in port[self.IF_ENTITY] and '.' not in port[self.IF_ENTITY]}
        self.logger.info('Loading Port Channels:')
        for key, value in port_channel_dic.iteritems():
            interface_model = value[self.IF_ENTITY]
            match_object = re.search(r'\d+$', interface_model)
            if match_object:
                interface_id = 'PC{0}'.format(match_object.group(0))
            else:
                self.logger.error('Adding of {0} failed. Name is invalid'.format(interface_model))
                continue
            attribute_map = {'description': self.snmp.get_property('IF-MIB', 'ifAlias', key),
                             'associated_ports': self._get_associated_ports(key)}
            attribute_map.update(self._get_ip_interface_details(key))
            port_channel = PortChannel(name=interface_model, relative_path=interface_id, **attribute_map)
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
            if str(item_id) in value['dot3adAggPortAttachedAggID']:
                result += self.if_table[key][self.IF_ENTITY].replace('/', '-').replace(' ', '') + '; '
        return result.strip(' \t\n\r')

    def _get_ports_attributes(self):
        """Get resource details and attributes for every port in self.port_list

        :return:
        """

        self.logger.info('Load Ports:')
        for port in self.port_list:
            if_table_port_attr = {'ifType': 'str', 'ifPhysAddress': 'str', 'ifMtu': 'int', 'ifSpeed': 'int'}
            if_table = self.if_table[self.port_mapping[port]].copy()
            if_table.update(self.snmp.get_properties('IF-MIB', self.port_mapping[port], if_table_port_attr))
            interface_name = self.if_table[self.port_mapping[port]][self.IF_ENTITY].replace("'", '')
            if interface_name == '':
                interface_name = self.entity_table[port]['entPhysicalName']
            if interface_name == '':
                continue
            interface_type = if_table[self.port_mapping[port]]['ifType'].replace('/', '').replace("'", '')
            attribute_map = {'l2_protocol_type': interface_type,
                             'mac': if_table[self.port_mapping[port]]['ifPhysAddress'],
                             'mtu': if_table[self.port_mapping[port]]['ifMtu'],
                             'bandwidth': if_table[self.port_mapping[port]]['ifSpeed'],
                             'description': self.snmp.get_property('IF-MIB', 'ifAlias', self.port_mapping[port]),
                             'adjacent': self._get_adjacent(self.port_mapping[port])}
            attribute_map.update(self._get_interface_details(self.port_mapping[port]))
            attribute_map.update(self._get_ip_interface_details(self.port_mapping[port]))
            port_object = Port(name=interface_name, relative_path=self.relative_path[port], **attribute_map)
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
        if self.ip_v4_table and len(self.ip_v4_table) > 1:
            for key, value in self.ip_v4_table.iteritems():
                if 'ipAdEntIfIndex' in value and int(value['ipAdEntIfIndex']) == port_index:
                    interface_details['ipv4_address'] = key
                break
        if self.ip_v6_table and len(self.ip_v6_table) > 1:
            for key, value in self.ip_v6_table.iteritems():
                if 'ipAdEntIfIndex' in value and int(value['ipAdEntIfIndex']) == port_index:
                    interface_details['ipv6_address'] = key
                break
        return interface_details

    def _get_interface_details(self, port_index):
        """Get interface attributes

        :param port_index: port index in ifTable
        :return interface_details: detected info for provided interface dict{'Auto Negotiation': '', 'Duplex': ''}
        """

        interface_details = {'duplex': 'Full', 'auto_negotiation': 'False'}
        try:
            auto_negotiation = self.snmp.get(('MAU-MIB', 'ifMauAutoNegAdminStatus', port_index, 1)).values()[0]
            if 'enabled' in auto_negotiation.lower():
                interface_details['auto_negotiation'] = 'True'
        except Exception as e:
            self.logger.error('Failed to load auto negotiation property for interface {0}'.format(e.message))
        for key, value in self.duplex_table.iteritems():
            if 'dot3StatsIndex' in value.keys() and value['dot3StatsIndex'] == str(port_index):
                interface_duplex = self.snmp.get_property('EtherLike-MIB', 'dot3StatsDuplexStatus', key)
                if 'halfDuplex' in interface_duplex:
                    interface_details['duplex'] = 'Half'
        return interface_details

    def _get_device_details(self):
        """Get root element attributes

        """

        self.logger.info('Load Switch Attributes:')
        result = {'system_name': self.snmp.get_property('SNMPv2-MIB', 'sysName', 0),
                  'vendor': 'Cisco',
                  'model': self._get_device_model(),
                  'location': self.snmp.get_property('SNMPv2-MIB', 'sysLocation', 0),
                  'contact': self.snmp.get_property('SNMPv2-MIB', 'sysContact', 0),
                  'version': ''}

        match_version = re.search(r'Version\s+(?P<software_version>\S+)\S*\s+',
                                  self.snmp.get_property('SNMPv2-MIB', 'sysDescr', 0))
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
        for key, value in self.cdp_table.iteritems():
            if 'cdpCacheDeviceId' in value and 'cdpCacheDevicePort' in value:
                if re.search(r'^\d+', str(key)).group(0) == interface_id:
                    result = '{0} through {1}'.format(value['cdpCacheDeviceId'], value['cdpCacheDevicePort'])
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
        snmp_object_id = self.snmp.get_property('SNMPv2-MIB', 'sysObjectID', 0)
        match_name = re.search(r'\.(?P<model>\d+$)', snmp_object_id)
        if match_name:
            model = match_name.groupdict()['model']
            if model in CISCO_RESOURCE_DRIVERS_MAP:
                result = CISCO_RESOURCE_DRIVERS_MAP[model].lower().replace('_', '').capitalize()
        if not result or result == '':
            self.snmp.load_mib(['CISCO-PRODUCTS-MIB', 'CISCO-ENTITY-VENDORTYPE-OID-MIB'])
            match_name = re.search(r'::(?P<model>\S+$)', self.snmp.get_property('SNMPv2-MIB', 'sysObjectID', '0'))
            if match_name:
                result = match_name.groupdict()['model'].capitalize()
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
