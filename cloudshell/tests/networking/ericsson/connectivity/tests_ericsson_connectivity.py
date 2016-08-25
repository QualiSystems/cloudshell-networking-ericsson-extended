from unittest import TestCase
from mock import MagicMock
from cloudshell.networking.ericsson.ericsson_connectivity_operations import EricssonConnectivityOperations


class TestEricssonConnectivityOperations(TestCase):
    def _get_handler(self):
        self.cli = MagicMock()
        self.api = MagicMock()
        self.logger = MagicMock()
        return EricssonConnectivityOperations(cli=self.cli, logger=self.logger, api=self.api,
                                              resource_name='resource_name')

    def test_apply_connectivity_changes_validates_request_parameter(self):
        request = """{
        "driverRequest" : {
            "actions" : [{
                    "connectionId" : "0b0f37df-0f70-4a8a-bd7b-fd21e5fbc23d",
                    "connectionParams" : {
                        "vlanId" : "435",
                        "mode" : "Access",
                        "vlanServiceAttributes" : [{
                                "attributeName" : "QnQ",
                                "attributeValue" : "False",
                                "type" : "vlanServiceAttribute"
                            }, {
                                "attributeName" : "CTag",
                                "attributeValue" : "",
                                "type" : "vlanServiceAttribute"
                            }, {
                                "attributeName" : "Isolation Level",
                                "attributeValue" : "Shared",
                                "type" : "vlanServiceAttribute"
                            }, {
                                "attributeName" : "Access Mode",
                                "attributeValue" : "Access",
                                "type" : "vlanServiceAttribute"
                            }, {
                                "attributeName" : "VLAN ID",
                                "attributeValue" : "435",
                                "type" : "vlanServiceAttribute"
                            }, {
                                "attributeName" : "Pool Name",
                                "attributeValue" : "",
                                "type" : "vlanServiceAttribute"
                            }, {
                                "attributeName" : "Virtual Network",
                                "attributeValue" : "435",
                                "type" : "vlanServiceAttribute"
                            }
                        ],
                        "type" : "setVlanParameter"
                    },
                    "connectorAttributes" : [],
                    "actionId" : "0b0f37df-0f70-4a8a-bd7b-fd21e5fbc23d_5dded658-3389-466a-a479-4b97a3c17ebd",
                    "actionTarget" : {
                        "fullName" : "sw9003-vpp-10-3.cisco.com/port-channel2",
                        "fullAddress" : "10.89.143.226/PC2",
                        "type" : "actionTarget"
                    },
                    "customActionAttributes" : [],
                    "type" : "setVlan"
                }
            ]
        }
        }"""
        handler = self._get_handler()
        handler.get_port_name = MagicMock(return_value='Ethernet 1/2')
        result = handler.apply_connectivity_changes(request)
        self.assertIsNotNone(result)
        self.assertIn('"type": "setVlan"', str(result))
        self.assertIn('"actionId": "0b0f37df-0f70-4a8a-bd7b-fd21e5fbc23d_5dded658-3389-466a-a479-4b97a3c17ebd"',
                      str(result))

    def test_get_port_name_returns_correct_ethernet_name(self):
        handler = self._get_handler()
        correct_result = 'Ethernet 1/23'
        port_full_address = '127.0.0.1/0/1/23'
        handler._get_resource_full_name = MagicMock(return_value='SSR8020/Chassis 0/Module 1/Ethernet Port 1-23')
        result = handler.get_port_name(port_full_address)
        self.assertEqual(result, correct_result)

    def test_get_port_name_returns_correct_port_channel_name(self):
        handler = self._get_handler()
        correct_result = 'port-channel 1'
        port_full_address = '127.0.0.1/0/1/PC1'
        handler._get_resource_full_name = MagicMock(return_value='SSR8020/Chassis 0/Module 1/port-channel 1')
        result = handler.get_port_name(port_full_address)
        self.assertEqual(result, correct_result)
