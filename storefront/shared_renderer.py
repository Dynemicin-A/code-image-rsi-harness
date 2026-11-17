"""Reusable semantic rainy-street compositor; no raster assets or target access."""
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
import argparse, json, math, time
from pathlib import Path
import numpy as np

def poly(draw, pts, fill, outline=None, width=1): draw.polygon(pts, fill=fill); outline and draw.line(pts+[pts[0]], fill=outline, width=width, joint="curve")
def lerp(a,b,t): return tuple(int(a[i]*(1-t)+b[i]*t) for i in range(3))

def fraction(value):
    if type(value) not in (float, int) or not 0 <= value <= 1:
        raise ValueError("window coordinates and opacity must be numbers in [0, 1]")
    return value


def rgb(color):
    if len(color) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in color):
        raise ValueError("window colors must have three integer channels in [0, 255]")
    return tuple(color)


def draw_display(image, window, display, light, frame_width):
    """Composite a program-authored display strictly inside one window frame."""
    x0, y0, x1, y1 = window
    left, top = x0 + frame_width, y0 + frame_width
    width, height = x1 - frame_width - left + 1, y1 - frame_width - top + 1
    if min(width, height) < 1:
        raise ValueError("window must be larger than its frame")
    pane = Image.new("RGB", (width, height), rgb(light))
    draw = ImageDraw.Draw(pane)
    for fixture in display["objects"]:
        u0, v0, u1, v1 = map(fraction, fixture["box"])
        if u0 >= u1 or v0 >= v1:
            raise ValueError("display object box must have positive width and height")
        for shape in fixture["shapes"]:
            points = [
                (round((u0 + (u1-u0)*fraction(u)) * (width-1)),
                 round((v0 + (v1-v0)*fraction(v)) * (height-1)))
                for u, v in shape["points"]
            ]
            color, kind = rgb(shape["color"]), shape["kind"]
            if kind in ("rectangle", "ellipse") and len(points) == 2:
                if points[0][0] > points[1][0] or points[0][1] > points[1][1]:
                    raise ValueError("rectangle and ellipse require ordered bounds")
                if kind == "rectangle":
                    draw.rectangle(points, fill=color)
                else:
                    draw.ellipse(points, fill=color)
            elif kind == "polygon" and len(points) >= 3:
                draw.polygon(points, fill=color)
            elif kind == "line" and len(points) >= 2:
                thickness = fraction(shape["width"])
                if thickness <= 0:
                    raise ValueError("line width must be positive")
                draw.line(points, fill=color, width=max(1, round(thickness * min(width, height))))
            else:
                raise ValueError(f"invalid display primitive or point count: {kind!r}")
    if "glass" in display:
        tint = display["glass"]
        pane = Image.blend(pane, Image.new("RGB", pane.size, rgb(tint["color"])),
                           fraction(tint["opacity"]))
    image.paste(pane, (left, top))

def render(scene):
    W=H=int(scene.get("size",1254)); seed=int(scene.get("seed",71)); rng=np.random.default_rng(seed)
    im=Image.new("RGB",(W,H)); p=im.load(); sky=scene["palette"]["sky"]; road=scene["palette"]["road"]
    horizon=int(scene["layout"]["horizon"]*H)
    # cool dusk-to-road base
    for y in range(H):
        if y<horizon: c=lerp(tuple(sky[0]),tuple(sky[1]),y/max(1,horizon))
        else: c=lerp(tuple(road[0]),tuple(road[1]),(y-horizon)/max(1,H-horizon))
        for x in range(W): p[x,y]=c
    d=ImageDraw.Draw(im,"RGBA")
    # receding left street buildings
    for b in scene["left_buildings"]:
        x0,y0,x1,y1=b["box"]; x0*=W;x1*=W;y0*=H;y1*=H
        d.polygon([(x0,y0),(x1,y0+25),(x1,y1),(x0,y1)],fill=tuple(b["color"])+(255,))
        for row in range(4):
            yy=y0+90+row*105
            for col in range(max(1,int((x1-x0)/70))):
                xx=x0+24+col*70
                d.rectangle((xx,yy,xx+14,yy+44),fill=(237,174,86,150),outline=(28,44,53,180),width=3)
    # bakery brick mass
    bx=int(scene["layout"]["bakery_x"]*W); d.rectangle((bx,0,W,int(.69*H)),fill=tuple(scene["palette"]["brick"])+(255,))
    # brick courses (procedural, non-raster)
    for y in range(18,int(.69*H),27):
        d.line((bx,y,W,y),fill=(45,38,31,110),width=2)
        off=0 if (y//27)%2 else 34
        for x in range(bx+off,W,69): d.line((x,y-25,x,y),fill=(48,38,31,85),width=2)
    # upper masonry bands/windows
    d.rectangle((bx,54,W,91),fill=(27,42,40,230)); d.rectangle((bx,91,W,108),fill=(9,27,29,240))
    for x in (470,760,1050): d.rectangle((x,-12,x+68,62),fill=(15,31,34,255),outline=(139,104,76,255),width=8)
    # awning canopy and stripes
    aw=scene["awning"]; x0,y0,x1,y1=[int(v*W) for v in aw["box"]]
    poly(d,[(x0,y0),(x1,y0),(W,y1),(x0-55,y1)],tuple(aw["cream"])+(255,))
    n=aw["stripes"]
    for i in range(n):
        if i%2==0:
            xa=x0+(x1-x0)*i/n; xb=x0+(x1-x0)*(i+1)/n
            poly(d,[(xa,y0),(xb,y0),(xb+45,y1),(xa+35,y1)],tuple(aw["green"])+(255,))
    d.rectangle((x0-55,y1,W,y1+58),fill=tuple(aw["green"])+(255,))
    for x in range(x0-55,W,70): d.ellipse((x,y1+40,x+70,y1+70),fill=tuple(aw["green"])+(255,))
    # glowing storefront windows
    glow=Image.new("RGBA",im.size,(0,0,0,0)); gd=ImageDraw.Draw(glow,"RGBA")
    window_light=tuple(scene.get("window_light",[232,145,56]))
    displays = scene.get("window_displays")
    if displays is not None and len(displays) != len(scene["windows"]):
        raise ValueError("provide a window display for each window")
    for win in scene["windows"]:
        a,b,c,e=[int(v*W) for v in win]
        gd.rectangle((a,b,c,e),fill=window_light+(185,))
        for rad,alpha in [(45,65),(100,28)]: gd.rectangle((a-rad,b-rad,c+rad,e+rad),fill=window_light+(alpha,))
    glow=glow.filter(ImageFilter.GaussianBlur(28)); im=Image.alpha_composite(im.convert("RGBA"),glow); d=ImageDraw.Draw(im,"RGBA")
    for index, win in enumerate(scene["windows"]):
        a,b,c,e=[int(v*W) for v in win]; d.rectangle((a,b,c,e),fill=(142,88,43,72),outline=(12,38,34,255),width=16)
        # shelves and bread silhouettes
        if displays is None:
            for yy in range(b+105,e-20,72):
                d.line((a+18,yy,c-15,yy),fill=(54,39,26,210),width=9)
                for xx in range(a+35,c-25,64): d.ellipse((xx,yy-27,xx+48,yy+6),fill=(238,158,69,220),outline=(91,56,27,180),width=2)
        else:
            draw_display(im, (a,b,c,e), displays[index], window_light, frame_width=16)
        # fogged glass streaks and droplets
        if displays is not None:
            condensate = Image.new("RGBA", (c-a+1, e-b+1))
            mist = ImageDraw.Draw(condensate)
            mist_color = lerp(window_light, (239, 244, 248), .75)
        for _ in range(170):
            xx=int(rng.integers(a+10,c-8)); yy=int(rng.integers(b+8,e-8)); rr=int(rng.integers(1,4))
            alpha = int(rng.integers(25,100))
            if displays is None:
                d.ellipse((xx,yy,xx+rr,yy+rr*2),fill=(242,213,162,alpha))
            else:
                mist.ellipse((xx-a,yy-b,xx-a+rr,yy-b+rr*2), fill=mist_color+(alpha,))
        if displays is not None:
            im.alpha_composite(condensate, (a,b))
    # bakery door
    d.rectangle((466,318,618,758),fill=(12,49,42,255),outline=(13,31,29,255),width=12)
    d.rectangle((500,359,586,595),fill=(214,132,46,115),outline=(7,28,27,255),width=9)
    d.line((543,359,543,595),fill=(8,29,27,255),width=6)
    # pendant lamps
    for x,y in scene["lamps"]:
        x=int(x*W);y=int(y*H); d.line((x,y-45,x,y),fill=(35,30,25,255),width=5); d.pieslice((x-39,y-18,x+39,y+35),180,360,fill=(54,47,38,255)); d.ellipse((x-30,y+8,x+30,y+22),fill=(255,239,164,255))
    # curb and road perspective/cobbles
    d.polygon([(0,786),(W,811),(W,918),(0,851)],fill=(43,43,40,255)); d.line((0,817,W,870),fill=(174,129,81,180),width=7)
    for y in range(886,H,34):
        t=(y-886)/(H-886); cell=int(34+40*t); off=(y//34%2)*cell//2
        d.line((0,y,W,y+int(6*t)),fill=(12,23,29,170),width=3)
        for x in range(-off,W,cell): d.line((x,y,x+int(8*t),min(H,y+31)),fill=(10,20,24,150),width=2)
    # reflected light broken vertical strokes
    for cx,col,wid in [(385,(245,25,43,190),70),(565,(255,157,53,180),100),(819,(255,174,72,190),150),(1090,(255,167,65,150),120)]:
        for _ in range(95):
            y=int(rng.uniform(820,H)); spread=(y-800)*.18; x=int(rng.normal(cx,spread)); length=int(rng.uniform(5,35)); d.line((x,y,x+length,y),fill=col,width=int(rng.integers(2,8)))
    # traffic light
    d.rectangle((337,116,386,278),fill=(10,17,18,255),outline=(48,48,42,255),width=7); d.line((361,278,361,713),fill=(20,27,27,255),width=13)
    d.ellipse((345,130,379,164),fill=(255,32,21,255),outline=(255,120,70,220),width=5)
    # parked car
    d.polygon([(7,533),(27,509),(91,500),(126,528),(144,597),(0,603)],fill=(112,134,145,255),outline=(29,48,61,255))
    d.polygon([(32,514),(86,511),(112,538),(25,539)],fill=(28,53,70,230)); d.ellipse((20,576,54,612),fill=(13,20,24,255)); d.ellipse((110,568,143,605),fill=(13,20,24,255))
    # person semantic articulated silhouette
    person=scene["person"]; px=int(person["x"]*W); py=int(person["ground_y"]*H); coat=tuple(person["coat"])+(255,)
    # umbrella first
    ux=px+30; uy=420; d.pieslice((ux-150,uy-57,ux+150,uy+95),180,360,fill=tuple(person["umbrella"])+(245,),outline=(154,180,202,230),width=4); d.line((ux,uy,ux+30,600),fill=(31,34,37,255),width=5)
    d.ellipse((px-22,466,px+35,527),fill=(74,43,28,255)); poly(d,[(px-40,515),(px+40,513),(px+78,788),(px-88,805)],coat,outline=(86,64,19,255),width=4)
    d.line((px-28,792,px-52,928),fill=(19,35,49,255),width=38); d.line((px+25,790,px+62,924),fill=(19,35,49,255),width=38)
    d.line((px-55,922,px-86,956),fill=(64,43,34,255),width=28); d.line((px+58,921,px+95,940),fill=(64,43,34,255),width=28)
    d.line((px+38,552,px+83,613),fill=coat,width=28); d.ellipse((px+72,600,px+91,622),fill=(177,121,81,255))
    # bicycle: wheels, frame, spokes
    bike=scene["bike"]; x1,y1,x2,y2=[int(v*W) for v in bike["wheels"]]
    r=int(bike["radius"]*W)
    for cx,cy in [(x1,y1),(x2,y2)]:
        d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(15,19,18,255),width=10); d.ellipse((cx-r+8,cy-r+8,cx+r-8,cy+r-8),outline=(150,149,133,255),width=3)
        for ang in np.linspace(0,math.tau,20,endpoint=False): d.line((cx,cy,cx+math.cos(ang)*(r-9),cy+math.sin(ang)*(r-9)),fill=(122,123,116,180),width=1)
    crank=((x1+x2)//2,y1+8); frame=tuple(bike.get("color",[5,67,53]))+(255,); d.line((x1,y1,x1+73,y1-106,crank[0],crank[1],x1,y1),fill=frame,width=10,joint="curve"); d.line((x1+73,y1-106,x2-34,y2-91,x2,y2),fill=frame,width=10,joint="curve"); d.line((x1+63,y1-113,x1+43,y1-146),fill=frame,width=8); d.line((x1+24,y1-146,x1+85,y1-146),fill=(29,28,24,255),width=7)
    # rain layer and wet sparkle
    for _ in range(scene.get("rain_count",2400)):
        x=int(rng.integers(0,W)); y=int(rng.integers(0,H)); ln=int(rng.integers(4,19)); d.line((x,y,x-2,y+ln),fill=(190,219,237,int(rng.integers(35,130))),width=1)
    for _ in range(600):
        x=int(rng.integers(0,W)); y=int(rng.integers(450,H)); d.ellipse((x,y,x+int(rng.integers(1,5)),y+int(rng.integers(1,4))),fill=(220,225,208,int(rng.integers(35,145))))
    # procedural multiscale material/film texture; seed-controlled, never stored samples
    rgb=np.asarray(im.convert("RGB"),dtype=np.float32)
    low=rng.normal(0,1,(max(2,H//24),max(2,W//24))).astype(np.float32)
    lowim=Image.fromarray(np.uint8(np.clip(low*32+128,0,255))).resize((W,H),Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(10))
    field=(np.asarray(lowim,dtype=np.float32)-128.0)/9.0
    grain=rng.normal(0,scene.get("grain",3.5),(H,W,1)).astype(np.float32)
    rgb=np.clip(rgb+field[...,None]+grain,0,255).astype(np.uint8)
    im=Image.fromarray(rgb,"RGB").filter(ImageFilter.GaussianBlur(scene.get("soften",0.45)))
    im=ImageEnhance.Contrast(im).enhance(scene.get("contrast",0.94))
    im=ImageEnhance.Color(im).enhance(scene.get("saturation",0.88))
    if "color_affine" in scene:
        # Generic 3x4 photographic calibration: 12 global values, never spatial samples.
        a=np.asarray(scene["color_affine"],dtype=np.float32)
        if a.shape==(3,4): a=a.T
        if a.shape!=(4,3): raise ValueError("color_affine must be 4x3 or 3x4")
        q=np.asarray(im,dtype=np.float32); q=np.concatenate([q,np.ones((H,W,1),np.float32)],axis=2)
        im=Image.fromarray(np.clip(q@a,0,255).astype(np.uint8),"RGB")
    return im

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--scene",required=True); ap.add_argument("--output",required=True); ap.add_argument("--receipt"); a=ap.parse_args(); t=time.perf_counter()
    scene=json.loads(Path(a.scene).read_text()); out=render(scene); out.save(a.output); elapsed=time.perf_counter()-t
    if a.receipt: Path(a.receipt).write_text(json.dumps({"render_seconds":elapsed,"seed":scene.get("seed"),"dependencies":["numpy","Pillow"]},indent=2)+"\n")
    print(json.dumps({"render_seconds":elapsed,"size":out.size,"mode":out.mode}))
if __name__=="__main__": main()
