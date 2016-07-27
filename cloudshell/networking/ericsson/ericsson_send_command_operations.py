from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE
from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER, API
import inject

from cloudshell.networking.operations.interfaces.send_command_interface import SendCommandInterface
from cloudshell.shell.core.context_utils import get_resource_name


class EricssonSendCommandOperations(SendCommandInterface):
    def __init__(self, resource_name=None, cli=None, logger=None, api=None):
        """Create CiscoIOSHandlerBase

        :param cli: CliService object
        :param logger: QsLogger object
        :param snmp: QualiSnmp object
        :param api: CloudShell Api object
        :param resource_name: resource name
        :return:
        """

        self.supported_os = []
        self._cli = cli
        self._logger = logger
        self._api = api
        try:
            self.resource_name = resource_name or get_resource_name()
        except Exception:
            raise Exception('CiscoHandlerBase', 'Failed to get resource_name.')

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

    def send_command(self, command, expected_str=None, expected_map=None, timeout=None, retries=None,
                     is_need_default_prompt=True, session=None):
        """Send command using cli service

        :param command: command to send
        :param expected_str: optional, custom expected string, if you expect something different from default prompts
        :param expected_map: optional, custom expected map, if you expect some actions in progress of the command
        :param timeout: optional, custom timeout
        :param retries: optional, custom retry count, if you need more than 5 retries
        :param is_need_default_prompt: default
        :param session:

        :return: session returned output
        :rtype: string
        """

        if session:
            response = self.cli.send_command(command=command, expected_str=expected_str, expected_map=expected_map,
                                             timeout=timeout, retries=retries,
                                             is_need_default_prompt=is_need_default_prompt, session=session)
        else:
            response = self.cli.send_command(command=command, expected_str=expected_str, expected_map=expected_map,
                                             timeout=timeout, retries=retries,
                                             is_need_default_prompt=is_need_default_prompt)
        return response

    def send_config_command(self, command, expected_str=None, expected_map=None, timeout=None, retries=None,
                            is_need_default_prompt=True):
        """Send list of config commands to the session

        :param command: list of commands to send

        :return session returned output
        :rtype: string
        """

        return self.cli.send_config_command(command, expected_str, expected_map, timeout, retries,
                                            is_need_default_prompt)



