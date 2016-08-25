import inject
from cloudshell.configuration.cloudshell_cli_binding_keys import CLI_SERVICE
from cloudshell.configuration.cloudshell_shell_core_binding_keys import LOGGER, API
from cloudshell.networking.operations.state_operations import StateOperations
from cloudshell.shell.core.context_utils import get_resource_name


class EricssonStateOperations(StateOperations):
    def __init__(self, cli=None, logger=None, api=None, resource_name=None):
        StateOperations.__init__(self)
        self._cli = cli
        self._logger = logger
        self._api = api
        self._resource_name = resource_name

    @property
    def logger(self):
        if self._logger is None:
            try:
                self._logger = inject.instance(LOGGER)
            except:
                raise Exception('EricssonStateOperations', 'Failed to get logger.')
        return self._logger

    @property
    def cli(self):
        if self._cli is None:
            try:
                self._cli = inject.instance(CLI_SERVICE)
            except:
                raise Exception('EricssonStateOperations', 'Failed to get cli_service.')
        return self._cli

    @property
    def api(self):
        if self._api is None:
            try:
                self._api = inject.instance(API)
            except:
                raise Exception('EricssonStateOperations', 'Failed to get api handler.')
        return self._api

    @property
    def resource_name(self):
        if self._resource_name is None:
            try:
                self._resource_name = get_resource_name()
            except:
                raise Exception('EricssonStateOperations', 'Failed to get api handler.')
        return self._resource_name

    def shutdown(self):
        pass
