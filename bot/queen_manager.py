from ares import AresBot
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from ares.behaviors.combat.individual.queen_spread_creep import QueenSpreadCreep


class QueenManager:
    def __init__(self, ai: "AresBot"):
        self.ai: AresBot = ai

    def update(self) -> None:
        for queen in self.ai.mediator.get_own_army_dict[UnitID.QUEEN]:
            self.ai.register_behavior(
                QueenSpreadCreep(
                    queen, self.ai.start_location, self.ai.mediator.get_enemy_nat
                )
            )