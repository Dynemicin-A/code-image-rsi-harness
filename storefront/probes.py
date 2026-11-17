"""Two deliberately public development tasks for the rainy storefront renderer.

Print a scene JSON for the normal renderer CLI. The scene authors choose the
window contents. The rendering API has no store-type field or preset switch.
"""
import argparse
import json
import math


def shape(kind, points, color, **options):
    return {"kind": kind, "points": points, "color": color, **options}


def fixture(box, shapes):
    return {"box": box, "shapes": shapes}


def shelf(height):
    return fixture([0, height, 1, height + .045], [
        shape("rectangle", [(0, 0), (1, 1)], [83, 54, 42]),
        shape("rectangle", [(0, 0), (1, .22)], [182, 138, 104]),
    ])


def flowers(box, petal):
    green = [50, 93, 71]
    shapes = []
    for x, y in [(.22, .36), (.52, .15), (.78, .31)]:
        shapes.append(shape("line", [(x, y), (.5, .8)], green, width=.012))
        shapes.append(shape("polygon", [(.48, .73), (x*.9, .51), (.52, .65)], green))
        for i in range(5):
            angle = i * math.tau / 5
            u, v = x + .09*math.sin(angle), y + .057*math.cos(angle)
            shapes.append(shape("ellipse", [(u-.08, v-.045), (u+.08, v+.045)], petal))
        shapes.append(shape("ellipse", [(x-.05, y-.03), (x+.05, y+.03)], [246, 203, 115]))
    shapes.append(shape("polygon", [(.32, .70), (.67, .70), (.71, .80),
                                   (.63, 1), (.37, 1), (.29, .80)], [57, 102, 104]))
    return fixture(box, shapes)


def books(box):
    shapes = []
    spines = [(.02, .08, .17, [43, 94, 108]), (.18, .21, .32, [135, 62, 63]),
              (.34, .03, .50, [70, 91, 67]), (.52, .18, .68, [192, 135, 71])]
    for left, top, right, color in spines:
        shapes.append(shape("rectangle", [(left, top), (right, 1)], color))
        shapes.append(shape("line", [(left, top+.11), (right, top+.11)],
                            [226, 195, 142], width=.009))
    shapes.append(shape("polygon", [(.72, .19), (.83, .16), (1, .97), (.88, 1)],
                        [95, 70, 111]))
    return fixture(box, shapes)


def scene_for(task):
    scene = {
        "size": 1254, "seed": 324,
        "palette": {"sky": [[53, 59, 83], [145, 137, 161]],
                    "road": [[68, 74, 84], [34, 39, 53]], "brick": [98, 68, 78]},
        "layout": {"horizon": .48, "bakery_x": .34},
        "left_buildings": [{"box": [0, .21, .28, .67], "color": [69, 76, 89]}],
        "awning": {"box": [.39, .19, .95, .24], "stripes": 9,
                   "cream": [217, 199, 178], "green": [88, 116, 101]},
        "windows": [[.52, .31, .71, .61], [.74, .31, .97, .61]],
        "window_light": [237, 172, 185],
        "lamps": [[.62, .32], [.85, .32]],
        "person": {"x": .23, "ground_y": .74, "coat": [166, 206, 210],
                   "umbrella": [78, 44, 92]},
        "bike": {"wheels": [.64, .77, .86, .77], "radius": .083, "color": [177, 91, 55]},
        "rain_count": 870, "grain": 2.5, "soften": .45, "contrast": .96, "saturation": .94,
    }
    if task == "legacy":
        return scene
    if task == "florist":
        scene["window_displays"] = [
            {"glass": {"color": [171, 131, 157], "opacity": .16}, "objects": [
                shelf(.85), flowers([.03, .25, .53, .85], [166, 62, 103]),
                flowers([.47, .43, .96, .85], [248, 211, 183])]},
            {"glass": {"color": [153, 137, 174], "opacity": .14}, "objects": [
                shelf(.87), flowers([.18, .18, .80, .87], [127, 79, 136])]},
        ]
    elif task == "bookshop":
        scene["seed"] = 819
        scene["window_light"] = [238, 205, 158]
        scene["palette"]["brick"] = [74, 83, 87]
        scene["windows"] = [[.53, .32, .72, .59], [.75, .34, .96, .61]]
        scene["window_displays"] = [
            {"objects": [shelf(.48), shelf(.84), books([.09, .21, .86, .48]),
                         books([.16, .58, .94, .84])]},
            {"glass": {"color": [131, 159, 158], "opacity": .19},
             "objects": [shelf(.8), books([.04, .45, .54, .8]), books([.57, .57, .98, .8])]},
        ]
    else:
        raise ValueError(f"unknown development task: {task}")
    return scene


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=["legacy", "florist", "bookshop"])
    print(json.dumps(scene_for(parser.parse_args().task), indent=2))
