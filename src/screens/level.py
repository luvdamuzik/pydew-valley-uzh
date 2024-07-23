import math
from collections.abc import Callable
from random import randint

import pygame
import pytmx
from pathfinding.core.grid import Grid as PF_Grid
from pathfinding.finder.a_star import AStarFinder as PF_AStarFinder

from src.enums import FarmingTool, GameState, Layer, Map
from src.groups import AllSprites
from src.gui.interface.emotes import PlayerEmoteManager, NPCEmoteManager
from src.npc.chicken import Chicken
from src.npc.cow import Cow
from src.npc.npc import NPC
from src.npc.setup import AIData
from src.overlay.overlay import Overlay
from src.overlay.sky import Sky, Rain
from src.overlay.soil import SoilLayer
from src.overlay.transition import Transition
from src.settings import (
    TILE_SIZE,
    SCALE_FACTOR,
    SCALED_TILE_SIZE,
    TEST_ANIMALS,
    TILE_SIZE,
    MapDict,
    ENABLE_NPCS,
    SoundDict,
    SETUP_PATHFINDING,
    GAME_MAP,
)
from src.sprites.base import Sprite, AnimatedSprite
from src.sprites.character import Character
from src.sprites.particle import ParticleSprite
from src.sprites.player import Player
from src.sprites.tree import Tree
from src.support import map_coords_to_tile, load_data, resource_path


class Level:
    def __init__(
            self, switch: Callable[[GameState], None], tmx_maps: MapDict,
            frames: dict[str, dict], sounds: SoundDict
    ):
        # main setup
        self.display_surface = pygame.display.get_surface()
        self.switch_screen = switch

        # pathfinding
        self.pf_matrix_size = (0, 0)
        self.pf_matrix = []
        self.pf_grid: PF_Grid | None = None
        self.pf_finder = PF_AStarFinder()

        # sprite groups
        self.entities: dict[str, Player] = {}
        self.npcs: dict[str, NPC] = {}
        self.animals = []
        self.all_sprites = AllSprites()
        self.collision_sprites = pygame.sprite.Group()
        self.tree_sprites = pygame.sprite.Group()
        self.interaction_sprites = pygame.sprite.Group()

        # assets
        self.font = pygame.font.Font(resource_path('font/LycheeSoda.ttf'), 30)
        self.frames = frames
        self.sounds = sounds
        self.tmx_maps = tmx_maps
        self.current_map = GAME_MAP

        # soil
        self.soil_layer = SoilLayer(
            self.all_sprites,
            tmx_maps[self.current_map],
            frames["level"],
            sounds
        )

        # weather
        self.sky = Sky()
        self.rain = Rain(self.all_sprites, frames['level'], self.get_map_size())
        self.raining = False

        # emotes
        self.emotes = self.frames["emotes"]
        self.player_emote_manager = PlayerEmoteManager(self.all_sprites, self.emotes)

        self.npc_emote_manager = NPCEmoteManager(self.all_sprites, self.emotes)

        # setup map
        self.setup()
        self.player = self.entities['Player']

        # day night cycle
        self.transition = Transition(self.reset, self.finish_reset)
        self.day_transition = False
        self.current_day = 0

        # weather
        self.sky = Sky()
        self.rain = Rain(
            self.all_sprites,
            frames["level"],
            (tmx_maps[self.current_map].width *
             TILE_SIZE *
             SCALE_FACTOR,
             tmx_maps[self.current_map].height *
             TILE_SIZE *
             SCALE_FACTOR))

        # overlays
        self.overlay = Overlay(self.player, frames['overlay'])
        self.shop_active = False

    # setup
    def setup(self):
        self.activate_music()

        if SETUP_PATHFINDING:
            self.pf_matrix_size = (self.tmx_maps[self.current_map].width,
                                   self.tmx_maps[self.current_map].height)
            self.pf_matrix = [
                [1 for _ in range(self.pf_matrix_size[0])]
                for _ in range(self.pf_matrix_size[1])
            ]

        if self.current_map == Map.FARM:
            self.setup_tile_layer('Lower ground', self.setup_environment)
            self.setup_tile_layer('Upper ground', self.setup_environment)
        else:
            self.setup_tile_layer('Ground', self.setup_environment)
            self.setup_tile_layer('Water_decoration', self.setup_environment)
            self.setup_tile_layer('Hills', self.setup_environment)
            self.setup_tile_layer('Paths', self.setup_environment)
            self.setup_tile_layer('House_ground', self.setup_house)
            self.setup_tile_layer('House_walls', self.setup_house)
            self.setup_tile_layer('House_furniture_bottom', self.setup_house)
            self.setup_tile_layer('House_furniture_top', self.setup_house)
            self.setup_tile_layer('Border', self.setup_border)

        self.setup_tile_layer('Water', self.setup_water)

        if self.current_map == Map.FARM:
            self.setup_object_layer(
                'Collidable objects', self.setup_collideable_object
            )
        else:
            self.setup_object_layer(
                'Decorative', self.setup_collideable_object
            )
            self.setup_object_layer(
                'Vegetation', self.setup_collideable_object
            )
            self.setup_object_layer(
                'Trees', self.setup_collideable_object
            )

        self.setup_object_layer('Interactions', self.setup_interaction)

        self.setup_object_layer('Collisions', self.setup_collision)
        self.setup_object_layer('Entities', self.setup_entity)

        if SETUP_PATHFINDING:
            AIData.setup(self.pf_matrix)

        if ENABLE_NPCS:
            self.setup_object_layer('NPCs', self.setup_npc)
            self.setup_emote_interactions()

        if TEST_ANIMALS:
            self.setup_object_layer("Animals", self.setup_animal)

    def setup_tile_layer(self, layer: str, setup_func: Callable[[tuple[int, int], pygame.Surface], None]):
        for x, y, surf in self.tmx_maps[self.current_map].get_layer_by_name(layer).tiles():
            x = x * TILE_SIZE * SCALE_FACTOR
            y = y * TILE_SIZE * SCALE_FACTOR
            pos = (x, y)
            setup_func(pos, surf)

    def setup_environment(self, pos: tuple[int, int], surf: pygame.Surface):
        image = pygame.transform.scale_by(surf, SCALE_FACTOR)
        Sprite(pos, image, self.all_sprites, Layer.LOWER_GROUND)

    def setup_house(self, pos: tuple[int, int], surf: pygame.Surface):
        image = pygame.transform.scale_by(surf, SCALE_FACTOR)
        Sprite(pos, image, self.all_sprites, Layer.MAIN)

    def setup_border(self, pos: tuple[int, int], surf: pygame.Surface):
        image = pygame.transform.scale_by(surf, SCALE_FACTOR)
        Sprite(pos, image, (self.all_sprites, self.collision_sprites), Layer.BORDER)

    def setup_water(self, pos: tuple[int, int], surf: pygame.Surface):
        image = self.frames['level']['animations']['water']
        AnimatedSprite(pos, image, self.all_sprites, Layer.WATER)

    def setup_object_layer(self, layer: str, setup_func: Callable[[tuple[int, int], pytmx.TiledObject], None]):
        for obj in self.tmx_maps[self.current_map].get_layer_by_name(layer):
            x = obj.x * SCALE_FACTOR
            y = obj.y * SCALE_FACTOR
            pos = (x, y)
            setup_func(pos, obj)

    def pf_matrix_setup_collision(self, pos: tuple[float, float], size: tuple[float, float]):
        """
        :param pos: Absolute position of collision rect (x, y)
        :param size: Absolute size of collision rect (width, height)
        """
        tile_x = int(pos[0] / TILE_SIZE)
        tile_y = int(pos[1] / TILE_SIZE)
        tile_w = math.ceil((pos[0] + size[0]) / TILE_SIZE) - tile_x
        tile_h = math.ceil((pos[1] + size[1]) / TILE_SIZE) - tile_y

        for w in range(tile_w):
            for h in range(tile_h):
                self.pf_matrix[tile_y + h][tile_x + w] = 0

    def setup_collideable_object(self, pos: tuple[int, int], obj: pytmx.TiledObject):
        image = pygame.transform.scale_by(obj.image, SCALE_FACTOR)

        if obj.name == 'Tree':
            apple_frames = self.frames['level']['objects']['apple']
            stump_frames = self.frames['level']['objects']['stump']

            Tree(pos, image, (self.all_sprites, self.collision_sprites, self.tree_sprites), obj.name, apple_frames, stump_frames)
        else:
            Sprite(pos, image, (self.all_sprites, self.collision_sprites))

        if SETUP_PATHFINDING:
            self.pf_matrix_setup_collision((obj.x, obj.y), (obj.width, obj.height))

    def setup_collision(self, pos: tuple[int, int], obj: pytmx.TiledObject):
        size = (obj.width * SCALE_FACTOR, obj.height * SCALE_FACTOR)
        image = pygame.Surface(size)
        Sprite(pos, image, self.collision_sprites)

        if SETUP_PATHFINDING:
            self.pf_matrix_setup_collision((obj.x, obj.y), (obj.width, obj.height))

    def setup_interaction(self, pos: tuple[int, int], obj: pytmx.TiledObject):
        size = (obj.width * SCALE_FACTOR, obj.height * SCALE_FACTOR)
        image = pygame.Surface(size)
        Sprite(pos, image, self.interaction_sprites, Layer.MAIN, obj.name)

    def setup_entity(self, pos: tuple[int, int], obj: pytmx.TiledObject):
        self.entities[obj.name] = Player(
            pos=pos,
            frames=self.frames['character']['rabbit'],
            groups=(self.all_sprites, self.collision_sprites,),
            collision_sprites=self.collision_sprites,
            apply_tool=self.apply_tool,
            interact=self.interact,
            emote_manager=self.player_emote_manager,
            sounds=self.sounds,
            font=self.font
        )

    def setup_npc(self, pos: tuple[int, int], obj: pytmx.TiledObject):
        self.npcs[obj.name] = NPC(
            pos=pos,
            frames=self.frames['character']['rabbit'],
            groups=(self.all_sprites, self.collision_sprites,),
            collision_sprites=self.collision_sprites,
            apply_tool=self.apply_tool,
            soil_layer=self.soil_layer,
            emote_manager=self.npc_emote_manager,
        )

    def setup_animal(self, pos, obj):
        if obj.name == "Chicken":
            self.animals.append(Chicken(
                pos=pos,
                frames=self.frames['entities']['chicken'],
                groups=(self.all_sprites, self.collision_sprites),
                collision_sprites=self.collision_sprites,
            ))
        elif obj.name == "Cow":
            self.animals.append(Cow(
                pos=pos,
                frames=self.frames['entities']['cow'],
                groups=(self.all_sprites, self.collision_sprites),
                collision_sprites=self.collision_sprites,

                player=self.entities['Player']
            ))
        else:
            print(f"Malformed animal object name \"{obj.name}\" in tilemap")

    def setup_emote_interactions(self):
        @self.player_emote_manager.on_show_emote
        def on_show_emote(emote: str):
            if self.player.focused_entity:
                npc = self.player.focused_entity
                npc.abort_path()

                self.npc_emote_manager.show_emote(npc, emote)

        @self.player_emote_manager.on_emote_wheel_opened
        def on_emote_wheel_opened():
            player_pos = self.player.rect.center
            distance_to_player = 5 * SCALED_TILE_SIZE
            npc_to_focus = None
            for npc in self.npcs.values():
                current_distance = ((player_pos[0] - npc.rect.center[0]) ** 2 +
                                    (player_pos[1] - npc.rect.center[1]) ** 2) ** .5
                if current_distance < distance_to_player:
                    distance_to_player = current_distance
                    npc_to_focus = npc
            if npc_to_focus:
                self.player.focus_entity(npc_to_focus)

        @self.player_emote_manager.on_emote_wheel_closed
        def on_emote_wheel_closed():
            self.player.unfocus_entity()

    def get_map_size(self):
        return (self.tmx_maps[self.current_map].width * TILE_SIZE * SCALE_FACTOR,
                self.tmx_maps[self.current_map].height * TILE_SIZE * SCALE_FACTOR)

    def activate_music(self):
        volume = 0.1
        try:
            volume = load_data('volume.json') / 1000
        except FileNotFoundError:
            pass
        self.sounds["music"].set_volume(volume)
        self.sounds["music"].play(-1)

    # plant collision
    def plant_collision(self):
        if self.soil_layer.plant_sprites:
            for plant in self.soil_layer.plant_sprites:

                is_player_near = plant.rect.colliderect(self.player.plant_collide_rect)

                if plant.harvestable and is_player_near:

                    # add resource
                    ressource = plant.seed_type
                    quantity = 3
                    self.player.add_resource(ressource, quantity)

                    # update grid
                    x, y = map_coords_to_tile(plant.rect.center)
                    tile = self.soil_layer.tiles.get((x, y))
                    if tile:
                        tile.planted = False

                    # remove plant
                    plant.kill()
                    self.create_particle(plant)

    def create_particle(self, sprite: pygame.sprite.Sprite):
        ParticleSprite(sprite.rect.topleft, sprite.image, self.all_sprites)

    def apply_tool(
            self, tool: FarmingTool, pos: tuple[int, int], entity: Character
    ):
        match tool:
            case FarmingTool.AXE:
                for tree in pygame.sprite.spritecollide(
                    entity,
                    self.tree_sprites,
                    False,
                    lambda spr, tree_spr: spr.axe_hitbox.colliderect(tree_spr.rect)
                ):
                    tree.hit(entity)
                    self.sounds["axe"].play()
            case FarmingTool.HOE:
                self.soil_layer.hoe(pos)
            case FarmingTool.WATERING_CAN:
                self.soil_layer.water(pos)
                self.sounds['water'].play()
            case _:  # All seeds
                self.soil_layer.plant(pos, tool, entity.inventory)

    def interact(self):
        collided_interactions = pygame.sprite.spritecollide(self.player, self.interaction_sprites, False)
        if collided_interactions:
            if collided_interactions[0].name == 'Bed':
                self.start_reset()
            if collided_interactions[0].name == 'Trader':
                self.switch_screen(GameState.SHOP)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.switch_screen(GameState.PAUSE)
                return True

        return False

    # reset
    def reset(self):
        self.current_day += 1

        self.sky.set_time(6, 0)  # set to 0600 hours upon sleeping

        # plants + soil
        for tile in self.soil_layer.tiles.values():
            if tile.plant:
                tile.plant.grow()
            tile.watered = False
            for sprite in self.soil_layer.water_sprites:
                sprite.kill()

        self.raining = randint(0, 10) > 7
        self.soil_layer.raining = self.raining
        if self.raining:
            for pos, tile in self.soil_layer.tiles.items():
                self.soil_layer.water(pos, play_sound=False)
                self.soil_layer.update_tile_image(tile, pos)

        # apples on the trees

        # No need to iterate using explicit sprites() call.
        # Iterating over a sprite group normally will do the same thing
        for tree in self.tree_sprites:
            for apple in tree.apple_sprites:
                apple.kill()
            tree.create_fruit()

        # sky
        self.sky.start_color = [255, 255, 255]

    def finish_reset(self):
        self.day_transition = False
        for entity in self.entities.values():
            entity.blocked = False

    def start_reset(self):
        self.day_transition = True
        for entity in self.entities.values():
            entity.blocked = True
            entity.direction = pygame.Vector2(0, 0)

    # draw
    def draw_overlay(self):
        current_time = self.sky.get_time()
        self.overlay.display(current_time)

    def draw(self, dt):
        self.display_surface.fill((130, 168, 132))
        self.all_sprites.draw(self.player.rect.center)
        self.draw_overlay()
        self.sky.display(dt)

    # update
    def update_rain(self):
        if self.raining and not self.shop_active:
            self.rain.update()

    def update_day(self):
        if self.day_transition:
            self.transition.play()
            self.sky.set_time(6, 0)   # set to 0600 hours upon sleeping

    def update(self, dt: float):
        # update
        self.plant_collision()
        self.update_rain()
        self.update_day()
        self.all_sprites.update(dt)

        # draw
        self.draw(dt)