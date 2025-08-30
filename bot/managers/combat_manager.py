from typing import Callable, Any

from cython_extensions.units_utils import cy_find_units_center_mass, cy_closest_to
from loguru import logger
from sc2.position import Point2
from sc2.units import Units
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from ares import AresBot
from ares.consts import (
    EngagementResult,
    LOSS_DECISIVE_OR_WORSE,
    VICTORY_CLOSE_OR_BETTER,
    UnitRole,
)
from bot.consts import RequestType, ATTACK_TARGET_IGNORE
from bot.managers.queen_bot_mediator import QueenBotMediator


class CombatManager:
    queen_bot_mediator: QueenBotMediator

    def __init__(self, ai: "AresBot"):
        self.ai: AresBot = ai

        self._should_be_aggressive: bool = False
        # 2 ways of getting aggressive
        self._combat_sim_aggressive: bool = False
        self._supply_aggressive: bool = False

        self.queen_bot_requests_dict: dict[RequestType, Callable] = {
            RequestType.GET_ATTACK_TARGET: lambda kwargs: self.attack_target,
            RequestType.GET_SHOULD_BE_AGGRESSIVE: lambda kwargs: self._should_be_aggressive,
        }

    @property
    def attack_target(self) -> Point2:
        enemy_units: Units = self.ai.enemy_units.filter(
            lambda u: u.type_id not in ATTACK_TARGET_IGNORE
            and not u.is_flying
            and not u.is_cloaked
            and not u.is_hallucination
        )
        center_mass, num_units = cy_find_units_center_mass(enemy_units, 12.5)
        enemy_structures: Units = self.ai.enemy_structures
        if enemy_units(UnitID.WIDOWMINEBURROWED):
            return cy_closest_to(self.ai.start_location, enemy_units).position
        elif num_units > 6:
            return Point2(center_mass)
        elif enemy_structures:
            return cy_closest_to(self.ai.start_location, enemy_structures).position
        elif enemy_units:
            return cy_closest_to(self.ai.start_location, enemy_units).position
        else:
            return self.ai.enemy_start_locations[0]

    def manager_request(
        self,
        receiver: str,
        request: RequestType,
        reason: str = None,
        **kwargs,
    ) -> Any:
        """Fetch information from this Manager so another Manager can use it.

        Parameters
        ----------
        receiver :
            This Manager.
        request :
            What kind of request is being made
        reason :
            Why the reason is being made
        kwargs :
            Additional keyword args if needed for the specific request, as determined
            by the function signature (if appropriate)

        Returns
        -------
        Optional[Union[Dict, DefaultDict, Coroutine[Any, Any, bool]]] :
            Everything that could possibly be returned from the Manager fits in there

        """
        return self.queen_bot_requests_dict[request](kwargs)

    def update(self) -> None:
        self._update_aggressive_status()

    def _update_aggressive_status(self):
        queens: Units = self.ai.mediator.get_own_army_dict[UnitID.QUEEN]
        num_queens: int = len(queens)
        if num_queens == 0:
            return

        if self.ai.mediator.get_units_from_role(role=UnitRole.QUEEN_NYDUS):
            self._should_be_aggressive = True
            return

        combat_sim_result: EngagementResult = self.ai.mediator.can_win_fight(
            own_units=queens, enemy_units=self.ai.mediator.get_cached_enemy_army
        )

        if self._combat_sim_aggressive:
            if combat_sim_result in LOSS_DECISIVE_OR_WORSE:
                logger.info(
                    f"{self.ai.time_formatted} - Turning off combat sim aggressive."
                )
                self._combat_sim_aggressive = False
        # only activate this if we have enough queens
        elif num_queens > 15:
            if combat_sim_result in VICTORY_CLOSE_OR_BETTER:
                logger.info(
                    f"{self.ai.time_formatted} - Turning on combat sim aggressive."
                )
                self._combat_sim_aggressive = True

        avg_energy: float = sum([unit.energy for unit in queens]) / num_queens
        if self._supply_aggressive:
            if self.ai.supply_army < 30 or avg_energy < 35:
                logger.info(
                    f"{self.ai.time_formatted} - Turning off supply aggressive."
                )
                self._supply_aggressive = False
        else:
            if self.ai.supply_used > 178 and avg_energy > 75:
                logger.info(f"{self.ai.time_formatted} - Turning on supply aggressive.")
                self._supply_aggressive = True

        self._should_be_aggressive = (
            self._combat_sim_aggressive or self._supply_aggressive
        )
