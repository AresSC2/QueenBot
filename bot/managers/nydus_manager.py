from typing import TYPE_CHECKING, List, Any

from cython_extensions.geometry import cy_distance_to_squared
from cython_extensions.units_utils import cy_find_units_center_mass
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.consts import TOWNHALL_TYPES_NO_PF, UnitTreeQueryType, DEBUG
from bot.consts import RequestType
from bot.managers.queen_bot_mediator import QueenBotMediator

if TYPE_CHECKING:
    from ares import AresBot


class NydusManager:
    """Manage Nydus Worms.

    Goals:
        - Use mediator to find good Nydus canal locations near enemy bases.
        - Prefer valid, visible, and safe placements.
        - Issue the build order from a ready Nydus Network when appropriate.
    """

    queen_bot_mediator: QueenBotMediator

    def __init__(self, ai: "AresBot"):
        self.ai: AresBot = ai

        self._base_to_nydus_tracker: dict[Point2, Any] = {}
        self.first_iteration: bool = True
        # keep track of whether we have placed a canal at the target base
        self._placed_canal_at_target_base: bool = True
        self._current_nydus_attack_target: Point2 = self.ai.enemy_start_locations[0]
        self._current_nydus_canal_target: Point2 = self.ai.enemy_start_locations[0]

        self.queen_bot_requests_dict: dict = {
            RequestType.GET_CURRENT_CANAL_TARGET: lambda kwargs: self._current_nydus_canal_target,
            RequestType.GET_CURRENT_NYDUS_TARGET: lambda kwargs: self._current_nydus_attack_target,
        }

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

    async def update(self) -> None:
        """Try to place a Nydus Worm at a good enemy location."""

        if not self.ai.start_location:
            return

        if self.first_iteration:
            self._current_nydus_canal_target = (
                self.ai.mediator.get_primary_nydus_enemy_main
            )
            self.first_iteration = False

        if self._placed_canal_at_target_base:
            self._find_new_nydus_location()

        await self._build_canal_at_target()
        # keep track if a canal exists at the target base
        self._update_nydus_tracker()

        await self._build_reinforcement_canals()

        if self.ai.config[DEBUG]:
            self.ai.draw_text_on_world(
                self._current_nydus_canal_target, "NYDUS CANAL TARGET"
            )

    def _nydus_network_ready_to_place_worm(self) -> Unit | None:
        own_structures_dict = self.ai.mediator.get_own_structures_dict
        networks: list[Unit] = [
            n
            for n in own_structures_dict[UnitID.NYDUSNETWORK]
            if n.is_ready and AbilityId.BUILD_NYDUSWORM in n.abilities
        ]
        if networks:
            return networks[0]
        return None

    async def _build_canal_at_target(self) -> None:
        if (
            self._current_nydus_attack_target not in self._base_to_nydus_tracker
            or not self.ai.can_afford(UnitID.NYDUSCANAL)
        ):
            return

        if network := self._nydus_network_ready_to_place_worm():
            target_base_info: dict = self._base_to_nydus_tracker[
                self._current_nydus_attack_target
            ]
            if target_base_info["nydus_exists"]:
                return

            location: Point2 = target_base_info["nydus_location"]
            close_canals: Units = self.ai.mediator.get_units_in_range(
                start_points=[location],
                distances=8.5,
                query_tree=UnitTreeQueryType.AllOwn,
            )[0].filter(lambda u: u.type_id == UnitID.NYDUSCANAL)

            if not close_canals and self.ai.is_visible(location):
                placement = await self.ai.find_placement(
                    UnitID.NYDUSCANAL, location, 3, False, 1
                )
                if placement and self.ai.is_visible(placement):
                    network(AbilityId.BUILD_NYDUSWORM, placement)

    async def _build_reinforcement_canals(self) -> None:
        if (
            not self.ai.can_afford(UnitID.NYDUSCANAL)
            or not self.queen_bot_mediator.get_should_be_aggressive
        ):
            return

        if network := self._nydus_network_ready_to_place_worm():
            if path := self.ai.mediator.find_raw_path(
                start=self.queen_bot_mediator.get_attack_target,
                target=self.ai.game_info.map_center,
                grid=self.ai.mediator.get_ground_grid,
                sensitivity=8,
            ):
                for point in path:
                    if self.ai.mediator.get_any_enemies_in_range(
                        positions=[point], radius=14.5
                    ):
                        continue
                    own_structures_dict: dict = self.ai.mediator.get_own_structures_dict
                    nyduses: list[Unit] = (
                        own_structures_dict[UnitID.NYDUSCANAL]
                        + own_structures_dict[UnitID.NYDUSNETWORK]
                    )
                    if (
                        len(
                            [
                                n
                                for n in nyduses
                                if cy_distance_to_squared(n.position, point) < 144.0
                            ]
                        )
                        > 0
                    ):
                        continue

                    placement = await self.ai.find_placement(
                        UnitID.NYDUSCANAL, point, 3, False, 1
                    )
                    if placement and self.ai.is_visible(placement):
                        network(AbilityId.BUILD_NYDUSWORM, placement)
                        break

    def _find_new_nydus_location(self):
        if not self.ai.mediator.get_cached_enemy_army:
            return

        own_structures_dict = self.ai.mediator.get_own_structures_dict
        networks: list[Unit] = own_structures_dict[UnitID.NYDUSNETWORK]
        if not networks:
            return

        # Place only if we have a ready network and no active canal
        if [
            n
            for n in networks
            if n.is_ready and AbilityId.BUILD_NYDUSWORM in n.abilities
        ]:
            # Build candidate bases: enemy main first, then known enemy expansions
            candidates: List[Point2] = [self.ai.enemy_start_locations[0]]
            enemy_townhalls: Units = self.ai.enemy_structures(TOWNHALL_TYPES_NO_PF)
            for base in enemy_townhalls:
                p: Point2 = base.position
                if p not in candidates and p not in self._base_to_nydus_tracker:
                    candidates.append(p)

            if not candidates:
                return

            # Determine enemy mass center
            pos, _ = cy_find_units_center_mass(
                self.ai.mediator.get_cached_enemy_army, distance=15.0
            )
            enemy_pos: Point2 = Point2(pos)

            target_base: Point2 = max(
                candidates,
                key=lambda candidate: cy_distance_to_squared(candidate, enemy_pos),
            )

            # we already are aware of this target base
            if (
                target_base == self._current_nydus_attack_target
                and target_base in self._base_to_nydus_tracker
            ):
                return

            # Ask mediator for a good Nydus spot near the target base
            spot: Point2 | None = self.ai.mediator.find_nydus_at_location(
                base_location=target_base,
                min_base_distance=15.0,
                max_nydus_distance=25.0,
                max_cost=20,
            )
            # check spot and that it's far enough away
            if (
                spot
                and cy_distance_to_squared(spot, enemy_pos) > 500.0
                and self.ai.mediator.is_position_safe(
                    grid=self.ai.mediator.get_ground_grid, position=spot
                )
            ):
                # enemy: Units = self.ai.mediator.get_units_in_range(
                #     start_points=[spot],
                #     distances=9.5,
                #     query_tree=UnitTreeQueryType.AllEnemy,
                # )[0]
                # if not enemy:
                self._base_to_nydus_tracker[target_base] = {
                    "nydus_location": spot,
                    "nydus_exists": False,
                }
                self._current_nydus_attack_target = target_base
                self._current_nydus_canal_target = spot

    def _update_nydus_tracker(self):
        keys_to_remove: list[Point2] = []
        for base_location, canal_info in self._base_to_nydus_tracker.items():
            location: Point2 = canal_info["nydus_location"]
            close_canals: Units = self.ai.mediator.get_units_in_range(
                start_points=[location],
                distances=8.5,
                query_tree=UnitTreeQueryType.AllOwn,
            )[0].filter(lambda u: u.type_id == UnitID.NYDUSCANAL)
            if canal_info["nydus_exists"]:
                # no canal close by, remove from tracker if not the target base
                if not close_canals:
                    if location == self._current_nydus_attack_target:
                        self._base_to_nydus_tracker[base_location][
                            "nydus_exists"
                        ] = False
                        self._placed_canal_at_target_base = False
                    else:
                        keys_to_remove.append(base_location)
            elif close_canals:
                if base_location == self._current_nydus_attack_target:
                    self._placed_canal_at_target_base = True
                self._base_to_nydus_tracker[base_location]["nydus_exists"] = True

            # no longer safe, remove
            elif not self.ai.mediator.is_position_safe(
                grid=self.ai.mediator.get_ground_grid, position=location
            ):
                self._placed_canal_at_target_base = True
                keys_to_remove.append(base_location)

        for key in keys_to_remove:
            del self._base_to_nydus_tracker[key]
