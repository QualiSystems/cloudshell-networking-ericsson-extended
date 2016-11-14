from collections import OrderedDict
import time

from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE, CONNECTION_MANAGER
from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER, API
import inject
import re
from cloudshell.networking.networking_utils import UrlParser
from cloudshell.networking.operations.configuration_operations import ConfigurationOperations
from cloudshell.shell.core.context_utils import get_resource_name, decrypt_password, get_attribute_by_name


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

    def _check_download_from_tftp(self, output, command='Copy'):
        """Verify if file was successfully uploaded
        :param output: output from cli
        :return True or False, and success or error message
        :rtype tuple
        """

        is_success = True
        status_match = re.search(r'^226\s+|Transfer\s+complete|100%.*[Kk][Bb]/s', output, re.IGNORECASE)
        message = ''
        if not status_match:
            is_success = False
            match_error = re.search(r"can't connect.*connection timed out|Error.*\n|[Ll]ogin [Ff]ailed", output,
                                    re.IGNORECASE)
            if match_error:
                self.logger.error(message)
                message += match_error.group().replace('%', '')

        if not is_success:
            raise Exception('EricssonConfigurationOperations',
                            '{0} Command failed: {1}'.format(command.title(), message))

    def save(self, folder_path=None, configuration_type='running', vrf_management_name=None):
        """Backup 'startup-config' or 'running-config' from device to provided file_system [ftp|tftp]
        Also possible to backup config to localhost
        :param folder_path:  tftp/ftp server where file be saved
        :param configuration_type: what file to backup
        :return: status message / exception
        """

        expected_map = dict()
        full_path = self.get_path(folder_path)

        url = UrlParser.parse_url(full_path)
        scheme = url.get(UrlParser.SCHEME, None)
        if scheme and 'scp' in scheme.lower():
            url[UrlParser.NETLOC] += '/'
            url[UrlParser.HOSTNAME] += '/'
        elif scheme and 'ftp' in scheme.lower():
            password = url.get(UrlParser.PASSWORD)
            if password:
                expected_map = {r'[Pp]assword\s*:': lambda session: session.send_line(password)}
                url.pop(UrlParser.PASSWORD)
                url[UrlParser.NETLOC] = url[UrlParser.NETLOC].replace(':{}'.format(password), '')
                url[UrlParser.NETLOC] += '/'
                url[UrlParser.HOSTNAME] += '/'

        if not configuration_type:
            configuration_type = 'running'
        if not re.search('startup|running', configuration_type, re.IGNORECASE):
            raise Exception('EricssonConfigurationOperations', "Source filename must be 'Running' or" +
                            " 'Startup'!")

        system_name = re.sub('\s+', '_', self.resource_name)
        if len(system_name) > 23:
            system_name = system_name[:23]

        destination_filename = '{0}-{1}-{2}'.format(system_name, configuration_type.lower(), _get_time_stamp())

        self.logger.info('destination filename is {0}'.format(destination_filename))

        url[UrlParser.FILENAME] = destination_filename

        destination_file_path = UrlParser.build_url(url)

        expected_map['overwrite'] = lambda session: session.send_line('y')
        expected_map['continue connecting'] = lambda session: session.send_line('yes')
        if 'startup' in configuration_type.lower():
            output = self.copy('ericsson.cfg', destination_file_path)
        else:
            output = self.cli.send_command('save configuration {0}'.format(destination_file_path),
                                           expected_map=expected_map)
        self._check_download_from_tftp(output)
        self.logger.info('Save configuration completed.')
        return destination_filename

    def restore(self, path, configuration_type, restore_method='override', vrf_management_name=None):
        """Restore configuration on device from provided configuration file
        Restore configuration from local file system or ftp/tftp server into 'running-config' or 'startup-config'.
        :param configuration_type: relative path to the file on the remote host tftp://server/sourcefile
        :param restore_method: override current config or not
        :return:
        """

        expected_map = {'overwrite': lambda session: session.send_line('y'),
                        'continue connecting': lambda session: session.send_line('yes')}
        if not re.search('append|override', restore_method.lower()):
            raise Exception('EricssonConfigurationOperations',
                            "Restore method '{}' is wrong! Use 'Append' or 'Override'".format(restore_method))

        match_data = re.search('startup|running', configuration_type, re.IGNORECASE)
        if not match_data:
            msg = "Configuration type '{}' is wrong, use 'startup' or 'running'.".format(
                configuration_type)
            raise Exception('EricssonConfigurationOperations', msg)
        destination_filename = match_data.group()

        self.logger.info('Start restore of device configuration from {}'.format(path))
        if 'startup' in destination_filename:
            if 'append' in restore_method.lower():
                raise Exception('EricssonConfigurationOperations',
                                'There is no startup configuration for {0}'.format(self.resource_name))
            self._override_startup_config(path, expected_map)
        else:
            if 'override' in restore_method.lower():
                self._configuration_override(path, expected_map)
            else:
                output = self._configure(path, expected_map=expected_map)
                self._check_download_from_tftp(output, 'configure')
        self.cli.commit()
        return 'Restore configuration completed.'

    def _override_startup_config(self, path, expected_map=None):
        """

        :param path:
        :param expected_map:
        :raise Exception:
        """

        output = self.copy(path, '/flash/.', expected_map)
        self._check_download_from_tftp(output)

        copied_file_name = path.split('/')[-1]
        output += self.cli.send_command('rename {0} admin.cfg -noconfirm'.format(copied_file_name),
                                        expected_map=expected_map)
        self.copy('admin.cfg', 'ericsson.cfg', expected_map)
        self._configure('/flash/admin.cfg')

    def _configuration_override(self, path, expected_map=None):
        """Configuration override

        :param path:
        :param expected_map:
        :return:
        """

        self._override_startup_config(path, expected_map)
        self.reload()

    def _configure(self, path, expected_map=None):
        """Configure command

        :param path:
        :param expected_map:
        :return:
        """

        return self.cli.send_command('configure {0}'.format(path), expected_map=expected_map)

    def copy(self, source, destination, expected_map=None, no_confirm=True):
        """Copy file from source to destination

        :param source:
        :param destination:
        :param expected_map:
        :param no_confirm:
        :return:
        """

        copy_command_str = 'copy {0} {1}'.format(source, destination)
        if no_confirm:
            copy_command_str += ' -noconfirm'

        return self.cli.send_command(copy_command_str, expected_map=expected_map)

    def reload(self, sleep_timeout=60, retries=15):
        """Reload device

        :param sleep_timeout: period of time, to wait for device to get back online
        :param retries: amount of retires to get response from device after it will be rebooted
        """

        expected_map = OrderedDict()
        expected_map['save.*configuration'] = lambda session: session.send_line('n')
        expected_map['[\[\(][Yy]es/[Nn]o[\)\]]|\[confirm\]'] = lambda session: session.send_line('yes')
        expected_map['\(y\/n\)|continue'] = lambda session: session.send_line('y')
        expected_map['[\[\(][Yy]/[Nn][\)\]]'] = lambda session: session.send_line('y')

        try:
            self.logger.info('Start installation of the new image:')
            self.cli.send_command(command='reload', expected_map=expected_map, timeout=3, retries=15,
                                  command_retries=1)

        except Exception:
            pass
        finally:
            session_type = self.cli.get_session_type()

            if not session_type == 'CONSOLE':
                self.logger.info('Session type is \'{}\', closing session...'.format(session_type))
                self.cli.destroy_threaded_session()
                connection_manager = inject.instance(CONNECTION_MANAGER)
                connection_manager.decrement_sessions_count()

        self.logger.info('Wait 20 seconds for device to reload...')
        time.sleep(20)

        retry = 0
        is_reloaded = False
        while retry < retries:
            retry += 1

            time.sleep(sleep_timeout)
            try:
                self.logger.debug('Trying to send command to device ... (retry {} of {}'.format(retry, retries))
                output = self.cli.send_command(command='', expected_str='(?<![#\n])[#>] *$', expected_map={}, timeout=5,
                                               retries=50, is_need_default_prompt=False)
                if len(output) == 0:
                    continue

                is_reloaded = True
                break
            except Exception as e:
                self.logger.error('EricssonFirmwareOperations', e.message)
                self.logger.debug('Wait {} seconds and retry ...'.format(sleep_timeout / 2))
                time.sleep(sleep_timeout / 2)
                pass

        return is_reloaded
