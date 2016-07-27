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


class EricssonConfigurationOperations(ConfigurationOperationsInterface, FirmwareOperationsInterface):
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

    def update_firmware(self, remote_host, file_path, size_of_firmware):
        pass

    def copy(self, source_file='', destination_file='', vrf=None, timeout=600, retries=5):
        """Copy file from device to tftp or vice versa, as well as copying inside devices filesystem

        :param source_file: source file.
        :param destination_file: destination file.
        :return tuple(True or False, 'Success or Error message')
        """

        host = None

        if '://' in source_file:
            source_file_data_list = re.sub('/+', '/', source_file).split('/')
            host = source_file_data_list[1]
            filename = source_file_data_list[-1]
        elif '://' in destination_file:
            destination_file_data_list = re.sub('/+', '/', destination_file).split('/')
            host = destination_file_data_list[1]
            filename = destination_file_data_list[-1]
        else:
            filename = destination_file

        if host and not validateIP(host):
            raise Exception('EricssonConfigurationOperations', 'Copy method: \'{}\' is not valid remote ip.'.format(host))

        copy_command_str = 'copy {0} {1}'.format(source_file, destination_file)
        if vrf:
            copy_command_str += ' vrf {0}'.format(vrf)

        expected_map = OrderedDict()
        if host:
            expected_map[host] = lambda session: session.send_line('')
        expected_map[r'{0}|\s+[Vv][Rr][Ff]\s+|\[confirm\]|\?'.format(filename)] = lambda session: session.send_line('')
        expected_map['\(y/n\)'] = lambda session: session.send_line('y')
        expected_map['\([Yy]es/[Nn]o\)'] = lambda session: session.send_line('yes')
        expected_map['bytes'] = lambda session: session.send_line('')

        output = self.cli.send_command(command=copy_command_str, expected_map=expected_map, timeout=60)
        output += self.cli.send_command('')

        return self._check_download_from_tftp(output)

    def _check_download_from_tftp(self, output):
        """Verify if file was successfully uploaded
        :param output: output from cli
        :return True or False, and success or error message
        :rtype tuple
        """
        is_success = True
        status_match = re.search(r'\d+ bytes copied|copied.*[\[\(].*[0-9]* bytes.*[\)\]]|[Cc]opy complete', output)
        message = ''
        if not status_match:
            is_success = False
            match_error = re.search('%.*|TFTP put operation failed.*', output, re.IGNORECASE)
            message = 'Copy Command failed. '
            if match_error:
                self.logger.error(message)
                message += match_error.group().replace('%', '')
            else:
                error_match = re.search(r"error.*\n|fail.*\n", output, re.IGNORECASE)
                if error_match:
                    self.logger.error(message)
                    message += match_error.group()

        return is_success, message

    def save_configuration(self, source_filename, timeout=30, vrf=None):
        """Replace config on target device with specified one

        :param source_filename: full path to the file which will replace current running-config
        :param timeout: period of time code will wait for replace to finish
        """

        if not source_filename:
            raise Exception('EricssonConfigurationOperations', "No source filename provided for config replace method!")
        command = 'configure replace ' + source_filename
        expected_map = {
            '[\[\(][Yy]es/[Nn]o[\)\]]|\[confirm\]': lambda session: session.send_line('yes'),
            '\(y\/n\)': lambda session: session.send_line('y'),
            '[\[\(][Nn]o[\)\]]': lambda session: session.send_line('y'),
            '[\[\(][Yy]es[\)\]]': lambda session: session.send_line('y'),
            '[\[\(][Yy]/[Nn][\)\]]': lambda session: session.send_line('y'),
            'overwritte': lambda session: session.send_line('yes')
        }
        output = self.cli.send_command(command=command, expected_map=expected_map, timeout=timeout)
        match_error = re.search(r'[Ee]rror:', output)

        if match_error is not None:
            error_str = output[match_error.end() + 1:] + '\n'
            error_str += error_str[:error_str.find('\n')]

            raise Exception('EricssonConfigurationOperations', 'Configure replace completed with error: ' + error_str)

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

        if source_filename == '':
            source_filename = 'running-config'
        if '-config' not in source_filename:
            source_filename = source_filename.lower() + '-config'
        if ('startup' not in source_filename) and ('running' not in source_filename):
            raise Exception('EricssonConfigurationOperations', "Source filename must be 'startup' or 'running'!")

        if destination_host == '':
            raise Exception('EricssonConfigurationOperations', "Destination host can\'t be empty.")

        system_name = re.sub('\s+', '_', self.resource_name)
        if len(system_name) > 23:
            system_name = system_name[:23]

        destination_filename = '{0}-{1}-{2}'.format(system_name, source_filename.replace('-config', ''),
                                                    _get_time_stamp())
        self.logger.info('destination filename is {0}'.format(destination_filename))

        if len(destination_host) <= 0:
            destination_host = self._get_resource_attribute(self.resource_name, 'Backup Location')
            if len(destination_host) <= 0:
                raise Exception('Folder path and Backup Location are empty.')

        if destination_host.endswith('/'):
            destination_file = destination_host + destination_filename
        else:
            destination_file = destination_host + '/' + destination_filename

        is_uploaded = self.copy(destination_file=destination_file, source_file=source_filename, vrf=vrf)
        if is_uploaded[0] is True:
            self.logger.info('Save configuration completed.')
            return '{0},'.format(destination_filename)
        else:
            # self.logger.info('is_uploaded = {}'.format(is_uploaded))
            self.logger.info('Save configuration failed with errors: {0}'.format(is_uploaded[1]))
            raise Exception(is_uploaded[1])

    def restore_configuration(self, source_file, config_type, restore_method='override', vrf=None):
        """Restore configuration on device from provided configuration file
        Restore configuration from local file system or ftp/tftp server into 'running-config' or 'startup-config'.
        :param source_file: relative path to the file on the remote host tftp://server/sourcefile
        :param restore_method: override current config or not
        :return:
        """

        if not re.search('append|override', restore_method.lower()):
            raise Exception('EricssonConfigurationOperations',
                            "Restore method '{}' is wrong! Use 'Append' or 'Override'".format(restore_method))

        if '-config' not in config_type:
            config_type = config_type.lower() + '-config'

        self.logger.info('Restore device configuration from {}'.format(source_file))

        match_data = re.search('startup-config|running-config', config_type)
        if not match_data:
            msg = "Configuration type '{}' is wrong, use 'startup-config' or 'running-config'.".format(config_type)
            raise Exception('EricssonConfigurationOperations', msg)

        destination_filename = match_data.group()

        if source_file == '':
            raise Exception('EricssonConfigurationOperations', "Source Path is empty.")

        if (restore_method.lower() == 'override') and (destination_filename == 'startup-config'):
            self.cli.send_command(command='del ' + destination_filename,
                                  expected_map={'\?|[confirm]': lambda session: session.send_line('')})

            is_uploaded = self.copy(source_file=source_file, destination_file=destination_filename, vrf=vrf)
        elif (restore_method.lower() == 'override') and (destination_filename == 'running-config'):

            if not self._check_replace_command():
                raise Exception('EricssonConfigurationOperations',
                                'Overriding running-config is not supported for this device.')

            self.configure_replace(source_filename=source_file, timeout=600, vrf=vrf)
            is_uploaded = (True, '')
        else:
            is_uploaded = self.copy(source_file=source_file, destination_file=destination_filename, vrf=vrf)

        if is_uploaded[0] is False:
            raise Exception('EricssonConfigurationOperations', is_uploaded[1])

        is_downloaded = (True, '')

        if is_downloaded[0] is True:
            return 'Restore configuration completed.'
        else:
            raise Exception('EricssonConfigurationOperations', is_downloaded[1])

    def _check_replace_command(self):
        """Checks whether replace command exist on device or not
        """

        output = self.cli.send_command('configure replace')
        if re.search(r'invalid (input|command)', output.lower()):
            return False
        return True