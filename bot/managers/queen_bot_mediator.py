from abc import ABCMeta, abstractmethod
from typing import Any, Callable

from sc2.position import Point2

from bot.consts import RequestType


class IQueenBotMediator(metaclass=ABCMeta):
    """
    The Mediator interface declares a method used by components to notify the
    mediator about various events. The Mediator may react to these events and
    pass the execution to other components (managers).
    """

    # each manager has a dict linking the request type to a callable action
    queen_bot_requests_dict: dict[RequestType, Callable]

    @abstractmethod
    def manager_request(
        self, receiver: str, request: RequestType, reason: str = None, **kwargs
    ) -> Any:
        """How requests will be structured.

        Parameters
        ----------
        receiver :
            The Manager the request is being sent to.
        request :
            The Manager that made the request
        reason :
            Why the Manager has made the request
        kwargs :
            If the ManagerRequest is calling a function, that function's keyword
            arguments go here.

        Returns
        -------
        Any

        """
        pass


class QueenBotMediator(IQueenBotMediator):
    def __init__(self) -> None:
        self.managers: dict = {}

    def add_managers(self, managers: list) -> None:
        """Generate manager dictionary.

        Parameters
        ----------
        managers :
            List of all Managers capable of handling ManagerRequests.
        """
        for manager in managers:
            self.managers[str(type(manager).__name__)] = manager
            manager.queen_bot_mediator = self

    def manager_request(
        self, receiver: str, request: RequestType, reason: str = None, **kwargs
    ) -> Any:
        """Function to request information from a manager.

        Parameters
        ----------
        receiver :
            Manager receiving the request.
        request :
            Requested attribute/function call.
        reason :
            Why the request is being made.
        kwargs :
            Keyword arguments (if any) to be passed to the requested function.

        Returns
        -------
        Any :
            There are too many possible return types to list all of them.

        """
        return self.managers[receiver].manager_request(
            receiver, request, reason, **kwargs
        )

    @property
    def get_attack_target(self) -> Point2:
        return self.manager_request("CombatManager", RequestType.GET_ATTACK_TARGET)

    @property
    def get_current_canal_target(self) -> Point2:
        return self.manager_request(
            "NydusManager", RequestType.GET_CURRENT_CANAL_TARGET
        )

    @property
    def get_current_nydus_target(self) -> Point2:
        return self.manager_request(
            "NydusManager", RequestType.GET_CURRENT_NYDUS_TARGET
        )

    @property
    def get_should_be_aggressive(self) -> bool:
        return self.manager_request(
            "CombatManager", RequestType.GET_SHOULD_BE_AGGRESSIVE
        )
