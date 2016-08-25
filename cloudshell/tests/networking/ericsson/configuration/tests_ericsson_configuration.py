from unittest import TestCase
import jsonpickle
from jsonschema import validate
from mock import MagicMock
from cloudshell.networking.ericsson.ericsson_configuration_operations import EricssonConfigurationOperations
from cloudshell.networking.networking_utils import UrlParser


class TestEricssonConfigurationOperations(TestCase):
    def _get_handler(self):
        self.cli = MagicMock()
        self.cli.send_command = MagicMock(return_value='226 Transfer complete')
        self.api = MagicMock()
        self.logger = MagicMock()
        return EricssonConfigurationOperations(cli=self.cli, logger=self.logger, api=self.api,
                                               resource_name='resource_name')

    # def test_orchestration_save_should_fail_startup_config(self):
    #     request = """
    #     {
    #         "custom_params": {
    #             "configuration_type" : "StartUp",
    #             "folder_path" : "tftp://10.0.0.1/folder1",
    #             "vrf_management_name": "network-1"
    #             }
    #     }"""
    #     handler = self._get_handler()
    #     self.assertRaises(Exception, handler.orchestration_save, custom_params=request)
    #
    # def test_orchestration_save_should_return_valid_response(self):
    #     request = """
    #     {
    #         "custom_params": {
    #             "configuration_type" : "Running",
    #             "folder_path" : "tftp://10.0.0.1/folder1",
    #             "vrf_management_name": "network-1"
    #             }
    #     }"""
    #     handler = self._get_handler()
    #     json_string = handler.orchestration_save(custom_params=request)
    #     validate(jsonpickle.loads(json_string), schema=get_schema())
    #
    # def test_orchestration_restore_validates_incoming_saved_artifact_info(self):
    #     saved_artifact_info = """{
    #     "saved_artifacts_info":
    #         {"saved_artifact":
    #             {
    #                 "artifact_type": "tftp",
    #                 "ftp_resource": "",
    #                 "identifier": "resource_name-running-250816-135543,",
    #                 "server": "10.0.0.1"
    #             },
    #             "resource_name": "resource_name",
    #             "restore_rules":
    #                 {
    #                     "requires_same_resource": true
    #                 },
    #             "created_date": "2016-08-25T13:55:43.105000"
    #         }
    #     }"""
    #
    #     restore = self._get_handler()
    #     restore.orchestration_restore(saved_artifact_info)
    #
    # def test_orchestration_restore_validates_wrong_saved_artifact_info(self):
    #     saved_artifact_info = """{
    #     "saved_artifacts_info":
    #         {"saved_artifact":
    #             {
    #                 "ftp_resource": "",
    #                 "identifier": "resource_name-running-250816-135543,",
    #                 "server": "10.0.0.1"
    #             },
    #             "resource_name": "resource_name",
    #             "restore_rules":
    #                 {
    #                     "requires_same_resource": true
    #                 },
    #             "created_date": "2016-08-25T13:55:43.105000"
    #         }
    #     }"""
    #
    #     restore = self._get_handler()
    #     self.assertRaises(Exception, restore.orchestration_restore, saved_artifact_info)

    def test_url_parser(self):
        url = 'ftp://user:pwd@google.com/folder1/file2'
        result = UrlParser.parse_url(url)
        self.assertIsNotNone(result)

    def test_url_join(self):
        correct_url = 'ftp://user:pwd@google.com/folder1/file2'
        url = '/folder1/file2'
        parsed_url = UrlParser.parse_url(url)
        parsed_url[UrlParser.HOSTNAME] = 'google.com'
        parsed_url[UrlParser.SCHEME] = 'ftp'
        parsed_url[UrlParser.USERNAME] = 'user'
        parsed_url[UrlParser.PASSWORD] = 'pwd'
        result = UrlParser.build_url(**parsed_url)
        self.assertIsNotNone(result)
        self.assertEqual(correct_url, result)


def get_schema():
    return {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "object",
        "definitions": {
            "artifact": {
                "type": "object",
                "properties": {
                    "artifact_type": {
                        "type": "string"
                    },
                    "identifier": {
                        "type": "string"
                    }
                },
                "required": [
                    "artifact_type",
                    "identifier"
                ]
            }
        },
        "properties": {
            "saved_artifacts_info": {
                "type": "object",
                "properties": {
                    "resource_name": {
                        "type": "string"
                    },
                    "created_date": {
                        "type": "string",
                        "format": "date-time"
                    },
                    "restore_rules": {
                        "type": "object",
                        "properties": {
                            "requires_same_resource": {
                                "type": "boolean"
                            }
                        },
                        "required": [
                            "requires_same_resource"
                        ]
                    },
                    "saved_artifact": {
                        "allOf": [
                            {
                                "$ref": "#/definitions/artifact"
                            },
                            {
                                "properties": {}
                            }
                        ],
                        "additionalProperties": True
                    }
                },
                "required": [
                    "resource_name",
                    "created_date",
                    "restore_rules",
                    "saved_artifact"
                ]
            }
        },
        "required": [
            "saved_artifacts_info"
        ]
    }
