"""Run in Unreal Editor: -ExecutePythonScript=<absolute path to this file>.

Creates a real map with a fixed camera, own primitive furniture and a runtime avatar actor.
Does not fabricate MetaHuman assets: AvatarClass and approved animation paths must be installed.
"""

import unreal

level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
assets.make_directory("/Game/Decanat")
if assets.does_asset_exist("/Game/Decanat/Office"):
    raise RuntimeError("Office already exists. This script never overwrites an edited scene.")
if not level.new_level("/Game/Decanat/Office"):
    raise RuntimeError("Cannot create Office map")

cube = unreal.load_asset("/Engine/BasicShapes/Cube.Cube")


def block(name, location, scale):
    actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*location))
    actor.set_actor_label(name)
    actor.static_mesh_component.set_static_mesh(cube)
    actor.set_actor_scale3d(unreal.Vector(*scale))
    return actor


block("Own desk surface", (100, 0, 75), (0.7, 1.5, 0.05))
for y in (-65, 65):
    block("Own desk support", (100, y, 37), (0.5, 0.05, 0.74))
block("Own chair seat", (-20, 0, 45), (0.5, 0.5, 0.04))
block("Own chair back", (-44, 0, 70), (0.04, 0.5, 0.5))
block("Own laptop base", (90, 0, 79), (0.25, 0.35, 0.02))
screen = block("Own laptop screen", (100, 0, 91), (0.02, 0.35, 0.25))
screen.set_actor_rotation(unreal.Rotator(0, 0, 0), False)
block("Floor", (0, 0, -5), (15, 15, 0.1))
block("Background", (-220, 0, 160), (0.1, 15, 3.2))
camera = actors.spawn_actor_from_class(unreal.CameraActor, unreal.Vector(430, -390, 170))
camera.set_actor_label("Fixed visitor camera")
camera.set_actor_rotation(
    unreal.MathLibrary.find_look_at_rotation(
        camera.get_actor_location(), unreal.Vector(15, 0, 100)
    ),
    False,
)
camera.camera_component.set_editor_property("field_of_view", 36.0)
for location, intensity in (((250, -180, 260), 2200.0), ((80, 240, 200), 1100.0)):
    light = actors.spawn_actor_from_class(unreal.RectLight, unreal.Vector(*location))
    light.set_actor_rotation(
        unreal.MathLibrary.find_look_at_rotation(
            light.get_actor_location(), unreal.Vector(0, 0, 120)
        ),
        False,
    )
    light.light_component.set_editor_property("intensity", intensity)
avatar_class = unreal.load_class(None, "/Script/Decanat.DecanatAvatar")
if avatar_class is None:
    raise RuntimeError("Build the Decanat C++ module before creating the scene")
actors.spawn_actor_from_class(avatar_class, unreal.Vector(0, 0, 0))
level.save_current_level()
unreal.log(
    "Office created. Real MetaHuman/animation assets and the local ACE provider still require configuration."
)
