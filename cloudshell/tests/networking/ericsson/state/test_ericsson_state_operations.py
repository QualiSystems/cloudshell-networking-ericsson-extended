from unittest import TestCase
from mock import MagicMock
from cloudshell.networking.ericsson.ericsson_state_operations import EricssonStateOperations


class TestEricssonConnectivityOperations(TestCase):
    def _get_handler(self):
        self.cli = MagicMock()
        self.api = MagicMock()
        self.logger = MagicMock()
        return EricssonStateOperations(cli=self.cli, logger=self.logger, api=self.api, resource_name='resource_name')

    def send_command_failed(self):
        raise Exception('CLI Failed')

    def _get_fail_handler(self):
        self.cli = MagicMock()
        self.api = MagicMock()
        self.logger = MagicMock()
        self.cli.send_command = self.send_command_failed
        return EricssonStateOperations(cli=self.cli, logger=self.logger, api=self.api, resource_name='resource_name')

    def test_health_check_pass(self):
        handler = self._get_fail_handler()
        result = handler.health_check()
        self.assertIsNotNone(result)
        self.assertIn('Health check on resource resource_name failed', result)

    def test_health_check_fail(self):
        handler = self._get_fail_handler()
        result = handler.health_check()
        self.assertIsNotNone(result)
        self.assertIn('Health check on resource resource_name failed', result)
