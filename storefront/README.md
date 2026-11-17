# 雨街橱窗共享接口

历史开发题要求把雨街面包房改为花店。程序可以改变招篷、雨衣和窗光，橱窗仍画面包。原接口没有商品输入，旧源码第 59 行起把面包直接画进每一扇窗。窗面另有固定褐色。修改窗光或增加迭代次数都无法改变商品几何。

旧开发记录位于同一 work 目录的 `clean_runs/probe_image_01/workspace/OUTPUT_INSPECTION.md`。本目录的 `shared_renderer.py` 衍生自该目录的同名公共源码，历史 SHA-256 为 `5ce02cd4517a31418a1b7c342b427dd88f6523c9fb4f174330a696f05eacdd2f`。本次两个场景均由 `probes.py` 用代码定义，所需依赖为 NumPy 和 Pillow。

## 场景接口

保留原有 `windows` 位置和外观设置。可选字段 `window_displays` 的元素与 `windows` 顺序一一对应。省略该字段时走旧版绘制路径。开启时，每扇窗以 `window_light` 为室内底色，依次绘制摆件，可再叠加一层有色玻璃。雾滴与窗光混色。

一个包含一层搁板的窗格可以写作

```json
{
  "objects": [
    {
      "box": [0.02, 0.8, 0.98, 0.84],
      "shapes": [
        {"kind": "rectangle", "points": [[0, 0], [1, 1]], "color": [75, 48, 34]}
      ]
    }
  ],
  "glass": {"color": [154, 137, 169], "opacity": 0.15}
}
```

`box` 使用窗框内归一化的左、上、右、下坐标。`points` 使用摆件内部归一化坐标。`rectangle`、`ellipse` 取左上和右下两点，`polygon` 接受至少三点，`line` 接受至少两点并使用 `width` 指定相对于窗格短边的线宽。图形按列表顺序绘制，可以在不同位置和比例复用。显式传入空的 `objects` 列表会渲染空橱窗。

## 运行与检查

在本目录使用装有 NumPy 和 Pillow 的 Python。临时目录中的两张程序图可直接复查。

```sh
out=$(mktemp -d)
for store in florist bookshop; do
    python3 -B probes.py "$store" > "$out/$store.json"
    python3 -B shared_renderer.py --scene "$out/$store.json" --output "$out/$store.png"
done
python3 -B -m unittest discover -v
```

本次使用 Python 3.13.3、NumPy 2.5.4、Pillow 13.0.0。通过普通命令行生成两张 1254 × 1254 RGB 图，逐窗检查花瓶、花瓣、书脊与架板。首次检查发现雾滴仍呈现硬编码黄色，调整凝露颜色和透明合成后重新出图核查。下表以解码后的 RGB 字节计算 SHA-256。

| 开发探针 | RGB SHA-256 |
| --- | --- |
| 花店，玫瑰色窗灯与两组不同花材 | `115b8212f5a6cd566148117c26754c903d97b50afe2cfbe70e636077a01df611` |
| 书店，另一组尺寸、种子、窗光与书架布局 | `6f08acbe55917100c4c835ef359e53ed6210033c48c74b4b7bea5f8b4b1fb655` |
| 省略新接口，同环境独立运行历史源码与新版，原生 RGB 字节完全相同 | `fe9d6dc42013012c4757ae356c01422692c7b6075b6433714deb5e43c2a8ce58` |

自动测试检查透明玻璃对像素的作用、错误输入、默认路径兼容，以及两种店型的摆件消融。消融时锁定构图、噪声、灯光和玻璃，关闭全局调色与模糊，比较有无摆件的两张原生渲染。断言覆盖目标颜色、两个窗格的面积变化和窗格外全部像素。上述结果只覆盖可见开发样例的橱窗表达。街道、人物和物体光照仍沿用固定的二维场景。未见提示词首稿质量及其与 diffusion 的差距仍待独立盲评。
