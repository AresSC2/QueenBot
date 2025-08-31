from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cython_extensions.units_utils import cy_closest_to
from loguru import logger
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit
from sc2.units import Units

from ares.cache import property_cache_once_per_frame
from ares.consts import UnitRole, DEBUG

if TYPE_CHECKING:
    from ares import AresBot

STEAL_FROM_ROLES: set[UnitRole] = {UnitRole.QUEEN_DEFENCE}


@dataclass
class QueenRoleController:
    ai: "AresBot"
    inject_queen_to_th: dict[int, int] = field(default_factory=dict)
    aggressive: bool = False
    detected_rush: bool = False

    @property_cache_once_per_frame
    def required_creep_spreaders(self) -> int:
        if self.ai.mediator.get_creep_coverage > 70.0:
            return 0

        if (
            self.ai.mediator.get_main_ground_threats_near_townhall
            and self.ai.get_total_supply(
                self.ai.mediator.get_main_ground_threats_near_townhall
            )
            >= 4.0
        ):
            return 0

        if (
            self.ai.mediator.get_main_air_threats_near_townhall
            and self.ai.get_total_supply(
                self.ai.mediator.get_main_air_threats_near_townhall
            )
            >= 6.0
        ):
            return 0

        num_queens: int = len(self.ai.mediator.get_own_army_dict[UnitTypeId.QUEEN])
        known_enemy_supply: float = self.ai.get_total_supply(
            self.ai.mediator.get_cached_enemy_army
        )
        if (
            known_enemy_supply / 0.8
        ) > num_queens * 2 or self.ai.mediator.get_did_enemy_rush:
            if num_queens > 12 or (
                self.ai.enemy_race == Race.Zerg and self.ai.mediator.get_enemy_expanded
            ):
                return 1
            else:
                return 0
        elif self.aggressive:
            return 1

        return 5

    @property_cache_once_per_frame
    def required_defenders(self) -> int:
        if self.aggressive:
            return 0
        num_queens: int = len(self.ai.mediator.get_own_army_dict[UnitTypeId.QUEEN])
        if self.ai.mediator.get_did_enemy_rush:
            if not self.detected_rush:
                self.detected_rush = True
                logger.info(f"{self.ai.time_formatted} - Detected rush")
            return num_queens

        known_enemy_supply: float = self.ai.get_total_supply(
            self.ai.mediator.get_cached_enemy_army
        )
        if known_enemy_supply >= 10 and known_enemy_supply > num_queens * 2:
            return num_queens

        return 0

    @property_cache_once_per_frame
    def required_injectors(self) -> int:
        if (
            self.ai.mediator.get_did_enemy_rush
            or self.aggressive
            or len(self.ai.townhalls) >= 5
            or len(self.ai.mediator.get_own_army_dict[UnitTypeId.QUEEN]) <= 3
        ):
            return 0

        return 1

    @property_cache_once_per_frame
    def required_nydus_queens(self) -> int:
        structures_dict: dict[
            UnitTypeId, list[Unit]
        ] = self.ai.mediator.get_own_structures_dict
        if (
            self.ai.supply_army > 30
            and len([structures_dict[UnitTypeId.NYDUSCANAL]]) > 0
            and len([s for s in structures_dict[UnitTypeId.NYDUSNETWORK] if s.is_ready])
            > 0
        ):
            return 6

        return 0

    def update(
        self,
        creep_queens: Units,
        defensive_queens: Units,
        inject_queens: Units,
        offensive_queens: Units,
        aggressive: bool,
    ) -> None:
        """
        General rule here:

        Queens get assigned `UnitRole.QUEEN_DEFENCE` by default in `assign_new_queen`
        So steal any queens from that role.

        If a queen is no longer needed in a role, assign it back to `UnitRole.QUEEN_DEFENCE`
        Then it can be stolen again if needed.

        If `aggressive` steal from QUEEN_DEFENCE, and change other roles to QUEEN_OFFENSIVE
        as required to get them to be aggressive

        Parameters
        ----------
        creep_queens
        defensive_queens
        inject_queens
        offensive_queens
        aggressive

        Returns
        -------

        """
        self.aggressive = aggressive

        if aggressive and defensive_queens:
            self.ai.mediator.switch_roles(
                from_role=UnitRole.QUEEN_DEFENCE, to_role=UnitRole.QUEEN_OFFENSIVE
            )
            return
        elif not aggressive and offensive_queens:
            self.ai.mediator.switch_roles(
                from_role=UnitRole.QUEEN_OFFENSIVE, to_role=UnitRole.QUEEN_DEFENCE
            )
            return

        self._manage_creep_role(defensive_queens, creep_queens)
        self._manage_inject_role(defensive_queens, inject_queens)
        self._manage_nydus_role(defensive_queens)

        if self.ai.config[DEBUG]:
            self._draw_debug_info()

    def assign_new_queen(self, queen: Unit) -> None:
        """
        Assign a new queen to a role
        Called from `bot/main.py -> bot/queen_manager.py -> QueenManager.assign_new_queen`
        """
        self.ai.mediator.assign_role(tag=queen.tag, role=UnitRole.QUEEN_DEFENCE)

    def _manage_creep_role(self, defensive_queens: Units, creep_queens: Units) -> None:
        num_required_spreaders: int = self.required_creep_spreaders
        # assign creep spreaders
        if (
            num_required_spreaders
            and len(creep_queens) < num_required_spreaders
            and defensive_queens
        ):
            queen_target: Unit = defensive_queens[0]
            self.ai.mediator.assign_role(
                tag=queen_target.tag, role=UnitRole.QUEEN_CREEP
            )

        # unassign creep spreaders
        if len(creep_queens) > num_required_spreaders:
            num_to_unassign: int = len(creep_queens) - num_required_spreaders
            for i in range(num_to_unassign):
                tag: int = creep_queens[i].tag
                self.ai.mediator.assign_role(tag=tag, role=UnitRole.QUEEN_DEFENCE)

    def _manage_inject_role(
        self, defensive_queens: Units, inject_queens: Units
    ) -> None:
        # assign injectors
        num_required_injectors: int = self.required_injectors
        if (
            num_required_injectors
            and len(inject_queens) < num_required_injectors
            and defensive_queens
        ):
            if townhalls_without_queen := [
                th
                for th in self.ai.townhalls
                if th.build_progress > 0.95 and th.tag not in self.inject_queen_to_th
            ]:
                queen_target: Unit = defensive_queens[0]
                th_target: Unit = cy_closest_to(
                    queen_target.position, townhalls_without_queen
                )
                self.ai.mediator.assign_role(
                    tag=queen_target.tag, role=UnitRole.QUEEN_INJECT
                )
                self.inject_queen_to_th[queen_target.tag] = th_target.tag

        # unassign injectors
        if len(inject_queens) > num_required_injectors:
            num_to_unassign: int = len(inject_queens) - num_required_injectors
            for i in range(num_to_unassign):
                tag: int = inject_queens[i].tag
                self.ai.mediator.assign_role(tag=tag, role=UnitRole.QUEEN_DEFENCE)
                if tag in self.inject_queen_to_th:
                    del self.inject_queen_to_th[tag]

    def _manage_nydus_role(self, defensive_queens: Units) -> None:
        num_required_nydus_queens: int = self.required_nydus_queens
        if (
            num_required_nydus_queens
            and len(self.ai.mediator.get_units_from_role(role=UnitRole.QUEEN_NYDUS))
            < num_required_nydus_queens
            and defensive_queens
        ):
            queen_target: Unit = defensive_queens[0]
            self.ai.mediator.assign_role(
                tag=queen_target.tag, role=UnitRole.QUEEN_NYDUS
            )

    def _draw_debug_info(self):
        # get queens based on roles
        creep_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP
        )
        for q in creep_queens:
            self.ai.draw_text_on_world(q.position3d, f"CREEP")
        inject_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_INJECT
        )
        for q in inject_queens:
            self.ai.draw_text_on_world(q.position3d, f"INJECT")
        defensive_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_DEFENCE
        )
        for q in defensive_queens:
            self.ai.draw_text_on_world(q.position3d, f"DEFENSIVE")
        offensive_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_OFFENSIVE
        )
        for q in offensive_queens:
            self.ai.draw_text_on_world(q.position3d, f"OFFENSIVE")
        nydus_queens: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.QUEEN_NYDUS
        )
        for q in nydus_queens:
            self.ai.draw_text_on_world(q.position3d, f"NYDUS")
