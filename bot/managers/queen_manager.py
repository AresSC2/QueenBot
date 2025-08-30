from typing import Callable, Any

from cython_extensions.geometry import cy_towards
from cython_extensions.units_utils import cy_center
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares import AresBot, UnitTreeQueryType
from ares.consts import UnitRole, VICTORY_MARGINAL_OR_BETTER
from ares.managers.squad_manager import UnitSquad
from bot.consts import RequestType
from bot.managers.queen_bot_mediator import QueenBotMediator
from bot.unit_control.combat_queens import CombatQueens
from bot.unit_control.creep_queens import CreepQueens

from bot.unit_control.base_control import BaseControl
from bot.managers.queen_role_controller import QueenRoleController
from bot.unit_control.inject_queens import InjectQueens
from bot.unit_control.nydus_queens import NydusQueens


class QueenManager:
    queen_bot_mediator: QueenBotMediator
    STEAL_FROM_ROLES: set[UnitRole] = {UnitRole.QUEEN_CREEP}
    STEAL_FROM_OL_ROLES: set[UnitRole] = {UnitRole.OVERLORD_CREEP_SPOTTER}

    def __init__(self, ai: "AresBot"):
        self.ai: AresBot = ai
        # controller to manage the queen roles
        self._queen_role_controller = QueenRoleController(ai)

        # combat classes
        self._creep_queens_control: BaseControl = CreepQueens(
            ai, ai.config, ai.mediator
        )
        self._inject_queens_control: BaseControl = InjectQueens(
            ai, ai.config, ai.mediator
        )
        self._combat_queens_control: BaseControl = CombatQueens(
            ai, ai.config, ai.mediator
        )
        self._nydus_queens_control: BaseControl = NydusQueens(
            ai, ai.config, ai.mediator
        )

        self.queen_bot_requests_dict: dict[RequestType, Callable] = {}

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
        aggressive: bool = self.queen_bot_mediator.get_should_be_aggressive

        # get queens based on roles
        creep_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP
        )
        inject_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_INJECT
        )
        defensive_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_DEFENCE
        )
        offensive_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_OFFENSIVE
        )
        nydus_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_NYDUS
        )

        # dynamically adjust existing queen roles
        self._queen_role_controller.update(
            creep_queens,
            defensive_queens,
            inject_queens,
            offensive_queens,
            aggressive=aggressive,
        )

        # control queens
        self._creep_queens_control.execute(creep_queens)
        self._inject_queens_control.execute(
            inject_queens,
            inject_q_to_th_tags=self._queen_role_controller.inject_queen_to_th,
        )
        main_ground_threats: Units = (
            self.ai.mediator.get_main_ground_threats_near_townhall
        )
        main_air_threats: Units = self.ai.mediator.get_main_air_threats_near_townhall
        can_engage: bool = (
            self.queen_bot_mediator.get_should_be_aggressive
            or main_ground_threats
            or main_air_threats
        )
        defensive_target: Point2 = Point2(
            cy_towards(self.ai.mediator.get_own_nat, self.ai.game_info.map_center, 5.9)
        )
        if main_ground_threats:
            defensive_target = Point2(cy_center(main_ground_threats))
        elif main_air_threats:
            defensive_target = Point2(cy_center(main_air_threats))
        self._combat_queens_control.execute(
            defensive_queens, target=defensive_target, can_engage=can_engage
        )

        if offensive_queens:
            attack_target: Point2 = self.queen_bot_mediator.get_attack_target
            squads: list[UnitSquad] = self.ai.mediator.get_squads(
                role=UnitRole.QUEEN_OFFENSIVE, squad_radius=9.0
            )
            if len(squads) > 0:
                pos_of_main_squad: Point2 = self.ai.mediator.get_position_of_main_squad(
                    role=UnitRole.QUEEN_OFFENSIVE
                )
                for squad in squads:
                    _target: Point2 = (
                        attack_target if squad.main_squad else pos_of_main_squad
                    )
                    self._combat_queens_control.execute(
                        squad.squad_units,
                        target=self.queen_bot_mediator.get_attack_target,
                        can_engage=can_engage,
                        check_close_combat_result=True,
                    )

        if nydus_queens:
            squads: list[UnitSquad] = self.ai.mediator.get_squads(
                role=UnitRole.QUEEN_NYDUS, squad_radius=9.0
            )
            nydus_target: Point2 = self.queen_bot_mediator.get_current_nydus_target
            can_engage_at_nydus: bool = self._check_nydus_engagement(nydus_queens)
            for squad in squads:
                self._nydus_queens_control.execute(
                    squad.squad_units,
                    nydus_target=nydus_target,
                    squad_pos=squad.squad_position,
                    can_engage_at_nydus=can_engage_at_nydus,
                )

    def assign_new_queen(self, queen: Unit) -> None:
        """
        Assign a new queen to a role
        Called from `bot/main.py`
        """
        self._queen_role_controller.assign_new_queen(queen)

    def _check_nydus_engagement(self, nydus_queens: Units) -> bool:
        enemy_near_nydus_target: Units = self.ai.mediator.get_units_in_range(
            start_points=[self.queen_bot_mediator.get_current_nydus_target],
            distances=[15.5],
            query_tree=UnitTreeQueryType.AllEnemy,
        )[0]

        if (
            self.ai.mediator.can_win_fight(
                own_units=nydus_queens, enemy_units=enemy_near_nydus_target
            )
            in VICTORY_MARGINAL_OR_BETTER
        ):
            return True

        return False
