from unittest import TestCase
from mock import MagicMock
from cloudshell.networking.ericsson.ericsson_firmware_operations import EricssonFirmwareOperations


class TestEricssonConfigurationOperations(TestCase):
    def _get_handler(self):
        self.cli = MagicMock()
        self.cli.send_command = MagicMock(side_effect=['Installation completed successfully', 'Reload Start'
                                                       'Reload Successfull', 'not null', '12.0.0SEOS_12'])
        self.api = MagicMock()
        self.logger = MagicMock()
        return EricssonFirmwareOperations(cli=self.cli, logger=self.logger, api=self.api,
                                               resource_name='resource_name')

    def test_load_firmware_parses_url(self):
        firmware_ops = self._get_handler()
        firmware_ops.load_firmware('tftp://10.10.10.10/Some folder/directory2/12.0.0SEOS_12.tar')

