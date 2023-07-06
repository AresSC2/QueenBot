from abc import ABC, abstractmethod
from bot.custom_bot_ai import CustomBotAI


class Manager(ABC):
    def __init__(self, bot: CustomBotAI) -> None:
        self.bot: CustomBotAI = bot

    @abstractmethod
    async def update(self, iteration: int) -> None:
        pass
