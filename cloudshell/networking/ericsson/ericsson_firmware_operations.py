import re
from collections import OrderedDict

import inject
import time

from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE, CONNECTION_MANAGER
from cloudshell.configuration.cloudshell_shell_core_binding_keys import API, LOGGER
from cloudshell.networking.networking_utils import UrlParser
from cloudshell.networking.operations.interfaces.firmware_operations_interface import FirmwareOperationsInterface
from cloudshell.shell.core.context_utils import get_resource_name


def rerun_image_loading(session, command):
    session.hardware_expect('n')
    session.send_line(command)


class EricssonFirmwareOperations(FirmwareOperationsInterface):
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

    def load_firmware(self, path, vrf_management_name=None):
        """Update firmware version on device by loading provided image, performs following steps:

        1. Copy bin file from remote tftp server.
        2. Clear in run config boot system section.
        3. Set downloaded bin file as boot file and then reboot device.
        4. Check if firmware was successfully installed.

        :param path: full path to firmware file on ftp/tftp location
        :param vrf_management_name: VRF Name
        :return: status / exception
        """

        url = UrlParser.parse_url(path)
        if not url \
                or UrlParser.FILENAME not in url \
                or not url[UrlParser.FILENAME] \
                or UrlParser.HOSTNAME not in url \
                or not url[UrlParser.HOSTNAME] \
                or UrlParser.SCHEME not in url \
                or not url[UrlParser.SCHEME]:
            raise Exception('EricssonFirmwareOperations', 'Path is wrong or empty')
        image_version = ''

        image_version_match = re.search(r'(?=\d)\S+(?=.tar)', url[UrlParser.FILENAME], re.IGNORECASE)
        if image_version_match:
            image_version = image_version_match.group()

        expected_map = OrderedDict()
        expected_map['download\s+in\s+progress\s+[\[\(][Yy]/[Nn][\)\]]'] = (
            lambda session: rerun_image_loading(session, 'release download {0}'.format(path)))
        expected_map['[\[\(][Yy]/[Nn][\)\]]'] = lambda session: session.send_line('y')
        expected_map['overwrite'] = lambda session: session.send_line('y')
        
        output = self.cli.send_command('release download {0}'.format(path), expected_map=expected_map)
        if not re.search('[Ii]nstallation [Cc]ompleted [Ss]uccessfully|Release distribution completed', output,
                         re.IGNORECASE):
            message = ''
            match_error = re.search("can't connect.*connection timed out|Error.*\n|[Ll]ogin [Ff]ailed|\S+\s+fail(ed)?" +
                                    "|release download already in progress, unable to continue",
                                    output, re.IGNORECASE)
            if match_error:
                message = match_error.group()
            raise Exception('EricssonConfigurationOperations',
                            'Failed to load firmware: {0}. Please see logs for details'.format(message))
        self.logger.info('Firmware has been successfully loaded to the device')

        if self.install_and_reboot():
            current_version = self.cli.send_command('show version | include Version')
            if image_version not in current_version:
                raise Exception('EricssonFirmwareOperations', 'Failed to install provided image, please check logs')
        return 'Success'

    def install_and_reboot(self, sleep_timeout=60, retries=15):
        """Reload device using new image

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
            self.cli.send_command(command='release upgrade', expected_map=expected_map, timeout=3, retries=15,
                                  command_retries=1)

        except Exception as e:
            session_type = self.cli.get_session_type()

            if not session_type == 'CONSOLE':
                self.logger.info('Session type is \'{}\', closing session...'.format(session_type))
                self.cli.destroy_threaded_session()
                connection_manager = inject.instance(CONNECTION_MANAGER)
                connection_manager.decrement_sessions_count()

        self.logger.info('Wait 20 seconds for device to reload...')
        time.sleep(20)

        retry = 0
        is_installed = False
        while retry < retries:
            retry += 1

            time.sleep(sleep_timeout)
            try:
                self.logger.debug('Trying to send command to device ... (retry {} of {}'.format(retry, retries))
                output = self.cli.send_command(command='', expected_str='(?<![#\n])[#>] *$', expected_map={}, timeout=5,
                                               retries=50, is_need_default_prompt=False)
                if len(output) == 0:
                    continue

                is_installed = True
                break
            except Exception as e:
                self.logger.error('EricssonFirmwareOperations', e.message)
                self.logger.debug('Wait {} seconds and retry ...'.format(sleep_timeout / 2))
                time.sleep(sleep_timeout / 2)
                pass

        return is_installed
