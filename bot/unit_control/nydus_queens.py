from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from cython_extensions.geometry import cy_distance_to_squared
from cython_extensions.units_utils import cy_closest_to

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.unit_control.combat_queens import CombatQueens

from ares.managers.manager_mediator import ManagerMediator

from bot.unit_control.base_control import BaseControl
from bot.unit_control.queens_movement import QueensMovement

if TYPE_CHECKING:
    from ares import AresBot, UnitTreeQueryType


@dataclass
class NydusQueens(BaseControl):
    """Execute behavior for nydus queens.

    Called from `QueenManager`

    Parameters
    ----------
    ai : AresBot
        Bot object that will be running the game
    config : Dict[Any, Any]
        Dictionary with the data from the configuration file
    mediator : ManagerMediator
        Used for getting information from managers in Ares.
    """

    ai: "AresBot"
    config: dict
    mediator: ManagerMediator

    def execute(self, units: Union[list[Unit], Units], **kwargs) -> None:
        """Execute the behavior."""
        if not units:
            return

        nydus_target: Point2 = kwargs.get("nydus_target", self.mediator.get_own_nat)
        squad_pos: Point2 = kwargs.get("squad_pos", units[0].position)
        can_engage_at_nydus: bool = kwargs.get("can_engage_at_nydus", False)

        close_to_target: bool = cy_distance_to_squared(squad_pos, nydus_target) < 450.0

        if close_to_target:
            if can_engage_at_nydus:
                CombatQueens(self.ai, self.config, self.mediator).execute(
                    units,
                    target=nydus_target,
                    can_engage=True,
                )
            else:
                QueensMovement(self.ai, self.config, self.mediator).execute(
                    units, target=self.mediator.get_own_nat
                )
        else:
            canals: list[Unit] = [
                u
                for u in self.mediator.get_own_structures_dict[UnitID.NYDUSCANAL]
                if u.is_ready
            ]
            networks: list[Unit] = [
                u
                for u in self.mediator.get_own_structures_dict[UnitID.NYDUSNETWORK]
                if u.is_ready
            ]
            if can_engage_at_nydus:
                target: Point2 = nydus_target
                if networks and len(canals) == 0:
                    target = cy_closest_to(squad_pos, networks).position
                QueensMovement(self.ai, self.config, self.mediator).execute(
                    units, target=target, exit_nydus_max_influence=10.0
                )
            else:
                QueensMovement(self.ai, self.config, self.mediator).execute(
                    units, target=self.mediator.get_own_nat
                )
