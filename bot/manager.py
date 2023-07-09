from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ares import AresBot


class Manager(ABC):
    def __init__(self, bot: "AresBot") -> None:
        self.bot: AresBot = bot

    @abstractmethod
    async def update(self, iteration: int) -> None:
        pass
