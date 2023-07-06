from typing import List

import numpy as np
from scipy import spatial

from bot.manager import Manager
from sc2.bot_ai import BotAI
from sc2.game_info import Ramp
from sc2.position import Point2
from sc2.units import Units


class TerrainManager(Manager):
    def __init__(self, bot: BotAI):
        super().__init__(bot)
        self.expansions: List[Point2] = []
        # this will get updated when `_calculate_nydus_spots` is run
        self.optimal_nydus_location: Point2 = self.bot.enemy_start_locations[0]

    @property
    def enemy_main_base_ramp(self) -> Ramp:
        """Works out which ramp is the enemies main

        Returns:
            Ramp -- SC2 Ramp object
        """
        return min(
            (
                ramp
                for ramp in self.bot.game_info.map_ramps
                if len(ramp.upper) in {2, 5}
            ),
            key=lambda r: self.bot.enemy_start_locations[0].distance_to(r.top_center),
        )

    @property
    def natural_location(self) -> Point2:
        if len(self.expansions) > 0:
            return self.expansions[0][0]

    @property
    def defensive_third(self) -> Point2:
        """
        Get the third furthest from enemy
        @return:
        """
        third_loc: Point2 = self.expansions[1][0]
        fourth_loc: Point2 = self.expansions[2][0]

        third_distance_to_enemy: float = third_loc.distance_to(
            self.bot.enemy_start_locations[0]
        )
        fourth_distance_to_enemy: float = fourth_loc.distance_to(
            self.bot.enemy_start_locations[0]
        )

        return (
            third_loc
            if third_distance_to_enemy >= fourth_distance_to_enemy
            else fourth_loc
        )

    async def update(self, iteration: int) -> None:
        if iteration == 0:
            self._calculate_nydus_spots()
            self.expansions = await self._path_expansion_distances()

    def get_behind_mineral_positions(self, th_pos: Point2) -> List[Point2]:
        """Thanks to sharpy"""
        positions: List[Point2] = []
        possible_behind_mineral_positions: List[Point2] = []

        all_mf: Units = self.bot.mineral_field.closer_than(10, th_pos)

        if all_mf:
            for mf in all_mf:
                possible_behind_mineral_positions.append(th_pos.towards(mf.position, 9))

            positions.append(th_pos.towards(all_mf.center, 9))  # Center
            positions.insert(
                0, positions[0].furthest(possible_behind_mineral_positions)
            )
            positions.append(positions[0].furthest(possible_behind_mineral_positions))
        else:
            positions.append(th_pos.towards(self.bot.game_info.map_center, 5))
            positions.append(th_pos.towards(self.bot.game_info.map_center, 5))
            positions.append(th_pos.towards(self.bot.game_info.map_center, 5))

        return positions

    def _calculate_nydus_spots(self) -> None:
        game_info = self.bot.game_info
        # create KDTree for nearest neighbor searching
        standin = [
            (x, y)
            for x in range(game_info.pathing_grid.width)
            for y in range(game_info.pathing_grid.height)
        ]
        tree = spatial.KDTree(standin)

        # get the height of the enemy main to make sure we find the right tiles
        enemy_height: float = game_info.terrain_height[
            self.bot.enemy_start_locations[0].rounded
        ]

        # find the gases and remove points with in 9 of them
        enemy_gases = self.bot.vespene_geyser.closer_than(
            12, self.bot.enemy_start_locations[0].rounded
        )
        gas_one = [
            standin[x] for x in tree.query_ball_point(enemy_gases[0].position, 11.5)
        ]
        gas_two = [
            standin[y] for y in tree.query_ball_point(enemy_gases[1].position, 11.5)
        ]
        close_to_gas = set(gas_one + gas_two)

        # find the enemy main base pathable locations
        enemy_main = [
            standin[z]
            for z in tree.query_ball_point(
                self.bot.enemy_start_locations[0].position, 45
            )
            if game_info.terrain_height[standin[z]] == enemy_height
            and game_info.pathing_grid[standin[z]] == 1
        ]

        # find the enemy ramp so we can avoid it
        close_to_ramp = [
            standin[z]
            for z in tree.query_ball_point(self.enemy_main_base_ramp.top_center, 18)
        ]

        # main base, but without points close to the ramp or gases
        main_away_from_gas_and_ramp = list(
            set(enemy_main) - close_to_gas - set(close_to_ramp)
        )

        # get a matrix of the distances from the points to the enemy main
        distances = spatial.distance_matrix(
            main_away_from_gas_and_ramp,
            [self.bot.enemy_start_locations[0].position.rounded],
        )

        # select the point with the greatest distance from the enemy main
        self.optimal_nydus_location = Point2(
            main_away_from_gas_and_ramp[np.where(distances == max(distances))[0][0]]
        )

        # get other positions in the enemy main for potential follow-up nyduses
        possible_nydus_locations = np.array(main_away_from_gas_and_ramp)[
            np.where(distances[:, 0] > 13)[0]
        ]
        edge = [
            Point2(loc)
            for loc in possible_nydus_locations
            if not all([game_info.pathing_grid[x] for x in Point2(loc).neighbors8])
        ]

        self.nydus_locations = edge

    async def _path_expansion_distances(self):
        """Calculates pathing distances to all expansions on the map"""
        expansion_distances = []
        for el in self.bot.expansion_locations_list:
            if (
                self.bot.start_location.distance_to(el)
                < self.bot.EXPANSION_GAP_THRESHOLD
            ):
                continue

            distance = await self.bot.client.query_pathing(self.bot.start_location, el)
            if distance:
                expansion_distances.append((el, distance))
        # sort by path length to each expansion
        expansion_distances = sorted(expansion_distances, key=lambda x: x[1])
        return expansion_distances
