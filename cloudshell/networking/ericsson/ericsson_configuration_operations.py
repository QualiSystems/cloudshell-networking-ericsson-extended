import time

from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE
from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER, API
import inject
import re
from cloudshell.networking.operations.configuration_operations import ConfigurationOperations
from cloudshell.shell.core.context_utils import get_resource_name


def _get_time_stamp():
    return time.strftime("%d%m%y-%H%M%S", time.localtime())


class EricssonConfigurationOperations(ConfigurationOperations):
    def __init__(self, cli=None, logger=None, api=None, resource_name=None):
        self._logger = logger
        self._api = api
        self._cli = cli
        self._resource_name = resource_name

    @property
    def resource_name(self):
        if not self._resource_name:
            try:
                self._resource_name = get_resource_name()
            except Exception:
                raise Exception('ConfigurationOperations', 'ResourceName is empty or None')
        return self._resource_name

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
            match_error = re.search(r"can't connect.*connection timed out|Error.*\n|[Ll]ogin [Ff]ailed", output,
                                    re.IGNORECASE)
            if match_error:
                self.logger.error(message)
                message += match_error.group().replace('%', '')

        return is_success, message

    def save(self, folder_path, configuration_type='Running', vrf_management_name=None):
        """Backup 'startup-config' or 'running-config' from device to provided file_system [ftp|tftp]
        Also possible to backup config to localhost
        :param destination_host:  tftp/ftp server where file be saved
        :param source_filename: what file to backup
        :return: status message / exception
        """

        expected_map = {}
        if not folder_path:
            folder_path = self._get_resource_attribute(self.resource_name, 'Backup Location')

        if not folder_path:
            raise Exception('EricssonConfigurationOperations', 'Folder Path parameter and Backup Location attribute ' +
                            'are empty!')

        if folder_path.startswith('ftp'):
            password = ''
            password_match = re.search('(?<=:)\S+?(?=\@)', folder_path.replace('ftp:', ''), re.IGNORECASE)
            if password_match:
                password = password_match.group()
                folder_path = folder_path.replace(':{0}'.format(password), '')
            expected_map[r'[Pp]assword\s*:'] = lambda session: session.send_line(password)

        if configuration_type == '':
            configuration_type = 'running'
        if not re.search('startup|running', configuration_type, re.IGNORECASE):
            raise Exception('EricssonConfigurationOperations', "Source filename must be 'Running' or" +
                            " 'Startup'!")

        if folder_path == '':
            raise Exception('EricssonConfigurationOperations', "Destination host can't be empty.")

        system_name = re.sub('\s+', '_', self.resource_name)
        if len(system_name) > 23:
            system_name = system_name[:23]

        destination_filename = '{0}-{1}-{2}'.format(system_name, configuration_type.lower(), _get_time_stamp())

        self.logger.info('destination filename is {0}'.format(destination_filename))

        if len(folder_path) <= 0:
            destination_host = self._get_resource_attribute(self.resource_name, 'Backup Location')
            if len(destination_host) <= 0:
                raise Exception('Folder path and Backup Location are empty.')

        if folder_path.endswith('/'):
            destination_file = folder_path + destination_filename
        else:
            destination_file = folder_path + '/' + destination_filename

        expected_map['overwrite'] = lambda session: session.send_line('y')
        if 'startup' in configuration_type.lower():
            # startup_config_file = self.cli.send_command('show configuration | include boot')
            # match_startup_config_file = re.search('\w+\.\w+', startup_config_file)
            # if not match_startup_config_file:
            #     raise Exception('EricssonConfigurationOperations', 'no startup/boot configuration found')
            # startup_config = match_startup_config_file.group()
            # command = 'copy {0} {1}'.format(startup_config, destination_file)
            raise Exception('EricssonConfigurationOperations',
                            'There is no startup configuration for {0}'.format(self.resource_name))
        else:
            command = 'save configuration {0}'.format(destination_file)
        output = self.cli.send_command(command, expected_map=expected_map)
        is_downloaded = self._check_download_from_tftp(output)
        if is_downloaded[0]:
            self.logger.info('Save configuration completed.')
            return '{0},'.format(destination_filename)
        else:
            self.logger.info('Save configuration failed with errors: {0}'.format(is_downloaded[1]))
            raise Exception('EricssonConfigurationOperations', 'Save configuration failed with errors:',
                            is_downloaded[1])

    def restore(self, path, configuration_type, restore_method='override', vrf_management_name=None):
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

        match_data = re.search('startup|running', configuration_type)
        if not match_data:
            msg = "Configuration type '{}' is wrong, use 'startup' or 'running'.".format(
                configuration_type)
            raise Exception('EricssonConfigurationOperations', msg)

        if path.startswith('ftp'):
            password = ''
            password_match = re.search('(?<=:)\S+?(?=\@)', path.replace('ftp:', ''), re.IGNORECASE)
            if password_match:
                password = password_match.group()
                path = path.replace(':{0}'.format(password), '')
            expected_map[r'[Pp]assword\s*:'] = lambda session: session.send_line(password)

        self.logger.info('Start restore of device configuration from {}'.format(path))
        destination_filename = match_data.group()

        expected_map['overwrite'] = lambda session: session.send_line('y')
        if 'startup' in destination_filename:
            # output = self.cli.send_command('copy {0} {1}'.format(source_file, 'startup-config.cfg'),
            #                                expected_map=expected_map)
            # output += self.cli.send_config_command('boot configuration startup-config.cfg', expected_map=expected_map)
            raise Exception('EricssonConfigurationOperations',
                            'There is no startup configuration for {0}'.format(self.resource_name))
        else:
            output = self.cli.send_command('configure {0}'.format(path), expected_map=expected_map)

        is_downloaded = self._check_download_from_tftp(output)
        if is_downloaded[0] is True:
            self.cli.commit()
            return 'Restore configuration completed.'
        else:
            raise Exception('EricssonConfigurationOperations', 'Restore Command failed: {0}'.format(is_downloaded[1]))
