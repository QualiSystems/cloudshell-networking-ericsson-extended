from cloudshell.networking.autoload.networking_autoload_resource_attributes import GenericResourceAttribute, \
    NetworkingStandardPortAttributes, NetworkingStandardModuleAttributes
from cloudshell.networking.autoload.networking_autoload_resource_structure import GenericResource, Port
from cloudshell.shell.core.driver_context import AutoLoadAttribute


class PFEResourceAttributes(GenericResourceAttribute):
    def __init__(self, relative_path, **kwargs):
        pass


class PFE(GenericResource):
    def __init__(self, name='', model='PFE', relative_path=''):
        self.attributes_class = PFEResourceAttributes
        GenericResource.__init__(self, name, model, relative_path)


class EricssonPortAttributes(NetworkingStandardPortAttributes):
    def __init__(self, relative_path, description='', l2_protocol_type='ethernet', mac='',
                 mtu=0, bandwidth=0, adjacent='', ipv4_address='', ipv6_address='', duplex='', auto_negotiation='',
                 supports_1ge=False, supports_10ge=False, supports_40ge=False, supports_100ge=False):
        NetworkingStandardPortAttributes.__init__(self, relative_path, description, l2_protocol_type, mac,
                                                  mtu, bandwidth, adjacent, ipv4_address, ipv6_address, duplex,
                                                  auto_negotiation)
        self.supports_1ge = AutoLoadAttribute(relative_path, 'Supports 1 Gigabit', supports_1ge)
        self.supports_10ge = AutoLoadAttribute(relative_path, 'Supports 10 Gigabit', supports_10ge)
        self.supports_40ge = AutoLoadAttribute(relative_path, 'Supports 40 Gigabit', supports_40ge)
        self.supports_100ge = AutoLoadAttribute(relative_path, 'Supports 100 Gigabit', supports_100ge)


class EricssonPort(GenericResource):
    def __init__(self, name='', model='Generic Port', relative_path='', **attributes_dict):
        port_name = name.replace('/', '-').replace('\s+', '')
        self.attributes_class = EricssonPortAttributes
        GenericResource.__init__(self, port_name, model, relative_path, **attributes_dict)


class EricssonModuleAttributes(NetworkingStandardModuleAttributes):
    def __init__(self, relative_path, serial_number='', ericsson_model='', module_model='', version=''):
        NetworkingStandardModuleAttributes.__init__(self, relative_path, serial_number, module_model, version)
        self.ericsson_model = AutoLoadAttribute(relative_path, 'Ericsson Model', ericsson_model)


class EricssonModule(GenericResource):
    def __init__(self, name='', model='Generic Module', relative_path='', **attributes_dict):
        self.attributes_class = EricssonModuleAttributes
        GenericResource.__init__(self, name, model, relative_path, **attributes_dict)
