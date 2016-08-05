import time
from collections import OrderedDict

from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE
from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER, API
import inject
import re
from cloudshell.networking.networking_utils import validateIP
from cloudshell.networking.operations.interfaces.configuration_operations_interface import \
    ConfigurationOperationsInterface
from cloudshell.networking.operations.interfaces.firmware_operations_interface import FirmwareOperationsInterface
from cloudshell.shell.core.context_utils import get_resource_name


def _get_time_stamp():
    return time.strftime("%d%m%y-%H%M%S", time.localtime())


class EricssonConfigurationOperations(ConfigurationOperationsInterface):
    def __init__(self, cli=None, logger=None, api=None, resource_name=None):
        self._logger = logger
        self._api = api
        self._cli = cli
        try:
            self.resource_name = resource_name or get_resource_name()
        except Exception:
            raise Exception('EricssonConfigurationOperations', 'ResourceName is empty or None')

    @property
    def logger(self):
        if self._logger:
            logger = self._logger
        else:
            logger = inject.instance(LOGGER)
        return logger

    @property
    def api(self):
        if self._api:
            api = self._api
        else:
            api = inject.instance(API)
        return api

    @property
    def cli(self):
        if self._cli is None:
            self._cli = inject.instance(CLI_SERVICE)
        return self._cli

    def _check_download_from_tftp(self, output):
        """Verify if file was successfully uploaded
        :param output: output from cli
        :return True or False, and success or error message
        :rtype tuple
        """
        is_success = True
        status_match = re.search(r'^226\s+|Transfer\s+complete', output, re.IGNORECASE)
        message = ''
        if not status_match:
            is_success = False
            match_error = re.search(r"can't connect.*connection timed out|Error.*\n|[Ll]ogin [Ff]ailed", output, re.IGNORECASE)
            if match_error:
                self.logger.error(message)
                message += match_error.group().replace('%', '')

        return is_success, message

    def _get_resource_attribute(self, resource_full_path, attribute_name):
        """Get resource attribute by provided attribute_name

        :param resource_full_path: resource name or full name
        :param attribute_name: name of the attribute
        :return: attribute value
        :rtype: string
        """

        try:
            result = self.api.GetAttributeValue(resource_full_path, attribute_name).Value
        except Exception as e:
            raise Exception(e.message)
        return result

    def save_configuration(self, destination_host, source_filename, vrf=None):
        """Backup 'startup-config' or 'running-config' from device to provided file_system [ftp|tftp]
        Also possible to backup config to localhost
        :param destination_host:  tftp/ftp server where file be saved
        :param source_filename: what file to backup
        :return: status message / exception
        """

        expected_map = {}
        if destination_host.startswith('ftp'):
            password = ''
            password_match = re.search('(?<=:)\S+?(?=\@)', destination_host.replace('ftp:',''), re.IGNORECASE)
            if password_match:
                password = password_match.group()
                destination_host = destination_host.replace(':{0}'.format(password), '')
            expected_map[r'[Pp]assword\s*:'] = lambda session: session.send_line(password)

        if source_filename == 'startup' or source_filename == 'running':
            source_filename += '-config'
        if source_filename == '':
            source_filename = 'configuration'
        if 'config' not in source_filename:
            raise Exception('EricssonConfigurationOperations', "Source filename must be 'running-config or" +
                            " startup-config'!")

        if destination_host == '':
            raise Exception('EricssonConfigurationOperations', "Destination host can\'t be empty.")

        system_name = re.sub('\s+', '_', self.resource_name)
        if len(system_name) > 23:
            system_name = system_name[:23]

        destination_filename = '{0}-{1}-{2}'.format(system_name, source_filename, _get_time_stamp())

        self.logger.info('destination filename is {0}'.format(destination_filename))

        if len(destination_host) <= 0:
            destination_host = self._get_resource_attribute(self.resource_name, 'Backup Location')
            if len(destination_host) <= 0:
                raise Exception('Folder path and Backup Location are empty.')

        if destination_host.endswith('/'):
            destination_file = destination_host + destination_filename
        else:
            destination_file = destination_host + '/' + destination_filename

        expected_map['overwrite'] = lambda session: session.send_line('y')
        if 'startup' in source_filename.lower():
            startup_config_file = self.cli.send_command('show configuration | include boot')
            match_startup_config_file = re.search('\w+\.\w+', startup_config_file)
            if not match_startup_config_file:
                raise Exception('EricssonConfigurationOperations', 'no startup/boot configuration found')
            startup_config = match_startup_config_file.group()
            command = 'copy {0} {1}'.format(startup_config, destination_file)
        else:
            command = 'save configuration {0}'.format(destination_file)
        output = self.cli.send_command(command, expected_map=expected_map)
        is_downloaded = self._check_download_from_tftp(output)
        if is_downloaded[0]:
            self.logger.info('Save configuration completed.')
            return '{0},'.format(destination_filename)
        else:
            self.logger.info('Save configuration failed with errors: {0}'.format(is_downloaded[1]))
            raise Exception('EricssonConfigurationOperations', 'Save configuration failed with errors:', is_downloaded[1])

    def restore_configuration(self, source_file, config_type, restore_method='override', vrf=None):
        """Restore configuration on device from provided configuration file
        Restore configuration from local file system or ftp/tftp server into 'running-config' or 'startup-config'.
        :param source_file: relative path to the file on the remote host tftp://server/sourcefile
        :param restore_method: override current config or not
        :return:
        """

        expected_map = {}

        if not re.search('append|override', restore_method.lower()):
            raise Exception('EricssonConfigurationOperations',
                            "Restore method '{}' is wrong! Use 'Append' or 'Override'".format(restore_method))

        if source_file.startswith('ftp'):
            password = ''
            password_match = re.search('(?<=:)\S+?(?=\@)', source_file.replace('ftp:',''), re.IGNORECASE)
            if password_match:
                password = password_match.group()
                source_file = source_file.replace(':{0}'.format(password), '')
            expected_map[r'[Pp]assword\s*:'] = lambda session: session.send_line(password)

        self.logger.info('Restore device configuration from {}'.format(source_file))

        match_data = re.search('startup|running?', config_type)
        if not match_data:
            msg = "Configuration type '{}' is wrong, use 'startup-config' or 'running-config'.".format(config_type)
            raise Exception('EricssonConfigurationOperations', msg)

        destination_filename = match_data.group()

        expected_map['overwrite'] = lambda session: session.send_line('y')
        if 'startup' in destination_filename:
            output = self.cli.send_command('copy {0} {1}'.format(source_file, 'startup-config.cfg'), expected_map=expected_map)
            output += self.cli.send_config_command('boot configuration startup-config.cfg', expected_map=expected_map)
        else:
            output = self.cli.send_command('configure {0}'.format(source_file), expected_map=expected_map)

        is_downloaded = self._check_download_from_tftp(output)
        if is_downloaded[0] is True:
            self.cli.commit()
            return 'Restore configuration completed.'
        else:
            raise Exception('EricssonConfigurationOperations', 'Restore Command failed: {0}'.format(is_downloaded[1]))
