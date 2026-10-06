import json, math, io, hashlib
from pathlib import Path
import streamlit as st
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

# Compatibility fix for older Python/OpenSSL builds where hashlib.md5() does not
# accept the usedforsecurity keyword expected by newer ReportLab releases.
import reportlab.pdfbase.pdfdoc as _reportlab_pdfdoc
_original_md5 = _reportlab_pdfdoc.md5
def _compat_reportlab_md5(data=b'', *args, **kwargs):
    return _original_md5(data)
_reportlab_pdfdoc.md5 = _compat_reportlab_md5
import matplotlib.pyplot as plt

# Business rules and prices live in presets.json; this app performs the calculations.
from matplotlib.patches import Rectangle


# Session-state defaults must exist before project-dependent code runs.
# Project state must exist before any calculation can read it.
if "project" not in st.session_state:
    st.session_state["project"] = []

DATA=Path(__file__).parent/'presets.json'
d=json.loads(DATA.read_text(encoding='utf-8'))
materials,edges,hardware,accessories,presets=(d[k] for k in ['materials','edge_bands','hardware','accessories','presets'])
settings=d.get('settings', {})
nesting_cfg=settings.get('nesting', {})
construction_cfg=settings.get('construction', {})
hardware_cfg=settings.get('hardware', {})
production_cfg=settings.get('production_costs', {})
margin_cfg=settings.get('margin', {})
custom_cfg=settings.get('custom_cabinet_defaults', {})


# Gola / plinth pricing is read directly from presets.json.
# The JSON is the single source of truth for bar length and price.
GOLA_BAR_PRICE = float(d['gola']['price'])
PLINTH_BAR_PRICE = float(d['plinthe']['price'])
GOLA_BAR_LENGTH = float(d['gola']['bar_length_m'])
PLINTH_BAR_LENGTH = float(d['plinthe']['bar_length_m'])

st.set_page_config(page_title='Al Moudir Mobilier',layout='wide')

# -----------------------------------------------------------------------------
# Authentication / roles
# -----------------------------------------------------------------------------
USERS_FILE = Path(__file__).parent / 'users.json'

def _hash_password(password, salt=None):
    if salt is None:
        salt = hashlib.sha256(hashlib.sha256(password.encode('utf-8')).digest()).hexdigest()[:32]
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 200_000).hex()
    return salt, digest

def _load_user_config():
    if not USERS_FILE.exists():
        salt, digest = _hash_password('admin123')
        USERS_FILE.write_text(json.dumps({
            'users': {'admin': {'password_hash': digest, 'salt': salt, 'role': 'admin', 'active': True}}
        }, indent=2), encoding='utf-8')
    try:
        return json.loads(USERS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}

def _load_users():
    return _load_user_config().get('users', {})

USER_MARGIN_STRATEGIES = ['X3', 'Margin per linear meter', 'Fixed margin', 'Margin per cubic meter']

def _user_default_margin_strategy():
    # Legacy compatibility only: there is no global margin-strategy setting.
    return 'X3'

def _user_margin_strategy(username):
    user = _load_users().get(username, {})
    strategy = user.get('margin_strategy', 'X3')
    return strategy if strategy in USER_MARGIN_STRATEGIES else 'X3'

def _save_users(users):
    config = _load_user_config()
    config['users'] = users
    USERS_FILE.write_text(json.dumps(config, indent=2), encoding='utf-8')

def _authenticate(username, password):
    users = _load_users()
    user = users.get(username)
    if not user or not user.get('active', True):
        return None
    salt = user.get('salt', '')
    _, digest = _hash_password(password, salt)
    return user if digest == user.get('password_hash') else None

def _logout():
    for key in ['authenticated', 'username', 'role']:
        st.session_state.pop(key, None)
    st.rerun()

def _login_screen():
    st.title('AL MOUDIR MOBILIER')
    st.subheader('Sign in')
    with st.form('login_form'):
        username = st.text_input('Username')
        password = st.text_input('Password', type='password')
        submitted = st.form_submit_button('Sign in', type='primary')
    if submitted:
        user = _authenticate(username.strip(), password)
        if user:
            st.session_state['authenticated'] = True
            st.session_state['username'] = username.strip()
            st.session_state['role'] = user.get('role', 'user')
            st.rerun()
        else:
            st.error('Invalid username or password.')

if not st.session_state.get('authenticated', False):
    _login_screen()
    st.stop()

ROLE = st.session_state.get('role', 'user')
IS_ADMIN = ROLE == 'admin'

# Default margin strategy for Users is X3. Admin keeps the normal selectable strategy.
# Track the role so a user login does not inherit an Admin's previous strategy.
if not IS_ADMIN:
    current_user_strategy = _user_margin_strategy(st.session_state.get('username', ''))
    if (st.session_state.get('_margin_strategy_role') != 'user' or
            st.session_state.get('_margin_strategy_user') != st.session_state.get('username') or
            st.session_state.get('margin_strategy') != current_user_strategy):
        st.session_state['margin_strategy'] = current_user_strategy
        st.session_state['_margin_strategy_role'] = 'user'
        st.session_state['_margin_strategy_user'] = st.session_state.get('username')
elif st.session_state.get('_margin_strategy_role') != 'admin':
    st.session_state['_margin_strategy_role'] = 'admin'
    st.session_state['_margin_strategy_user'] = st.session_state.get('username')

st.title('AL MOUDIR MOBILIER')
user_col, action_col = st.columns([8,1])
user_col.caption(f"Signed in as **{st.session_state.get('username','')}** · {'Administrator' if IS_ADMIN else 'User'}")
if action_col.button('Logout'):
    _logout()
if 'project' not in st.session_state: st.session_state.project=[]
if 'cfg' not in st.session_state: st.session_state.cfg={'screws':production_cfg['screw_boxes'],'bits':production_cfg['router_bits'],'led_al_m':2.0,'days':production_cfg['manufacturing_days'],'hand':production_cfg['manufacturing_labor'],'install':production_cfg['client_installation'],'transport':production_cfg['transport'],'rent':production_cfg['rent_per_day'],'margin_m':margin_cfg['default_per_linear_meter'],'fixed':margin_cfg['default_fixed_project'],'margin_m3':margin_cfg.get('default_per_cubic_meter',40000)}
st.session_state.setdefault('gola_bar_length', GOLA_BAR_LENGTH)
st.session_state.setdefault('plinthe_bar_length', PLINTH_BAR_LENGTH)
st.session_state.setdefault('gola_bar_price', GOLA_BAR_PRICE)
st.session_state.setdefault('plinthe_bar_price', PLINTH_BAR_PRICE)
st.session_state.setdefault('project_name', '')
st.session_state.setdefault('client_name', '')
st.session_state.setdefault('project_reference', '')
st.session_state.setdefault('project_notes', '')
st.session_state.setdefault('project_result', None)
st.session_state.setdefault('project_hinge_brand', list(hardware['Hinge'])[0])
st.session_state.setdefault('project_amortisseur_brand', list(hardware['Amortisseur'])[0])
st.session_state.setdefault('project_drawer_runner_brand', list(hardware['Drawer runner'])[0])
st.session_state.setdefault('project_hanging_box_brand', list(hardware['Hanging box'])[0])
if 'hinge_limits' not in st.session_state: st.session_state.hinge_limits=list(hardware_cfg['hinge_limits_mm'])


def is_front_visible_part(part_name):
    """Return True only for visible front/facade pieces."""
    n = str(part_name).lower()
    return ("door" in n or "drawer front" in n or "front" in n)


def front_edge_length(part):
    """Front-visible structural edge length, in mm."""
    name = str(part.get("name", "")).lower()
    w = float(part.get("w", 0))
    h = float(part.get("h", 0))
    if any(k in name for k in ("top", "bottom", "shelf", "étagère")):
        return w
    if "side" in name or "côté" in name:
        return h if h else w
    return 0.0

# --- Convert one cabinet preset into CNC parts ---
def make_parts(c,q):
    # Finishing panel is a true 2D panel: editable width/height only, no
    # cabinet depth or cabinet construction rules. It uses the facade material.
    if c.get('type') == '2d_panel':
        W=float(c['width']); H=float(c['height']); f=c.get('facade',c.get('material'))
        edge_enabled=c.get('edge_door',construction_cfg['door_edge_enabled'])
        edge=2*(W+H)/1000*edge_enabled
        return [{'name':'Finishing panel','qty':q,'w':round(W),'h':round(H),'material':f,
                 'edge_m':round(edge,3),'edge_type':'Door','category':'2D Panel','glass':False}]

    W,H,D=c['width'],c['height'],c['depth']; m=c['material']; f=c.get('facade',m); back=c.get('back',m); out=[]
    def add(name,n,w,h,mat,edge=0,cat='Structure',glass=False): out.append({'name':name,'qty':n,'w':round(w),'h':round(h),'material':mat,'edge_m':round(edge,3),'edge_type':'Door' if cat=='Door' else 'Structure','category':cat,'glass':glass})
    material_cfg=materials.get(m,{})
    material_thickness=float(material_cfg.get('thickness_mm',0) or 0)
    side_height=max(0,H-material_thickness)
    se=c.get('edge_side',construction_cfg['side_edge_enabled'])*side_height/1000
    add('Côté gauche',q,D,side_height,m,se); add('Côté droit',q,D,side_height,m,se)
    te=c.get('edge_struct',construction_cfg['structure_edge_enabled'])*(W+D)/1000
    top_cfg=c.get('top',{}) or {}
    if top_cfg.get('type')=='strips':
        strip_w=float(top_cfg.get('width_mm',100))
        strip_qty=int(top_cfg.get('quantity',2))
        strip_qty=max(1,strip_qty)
        strip_length=max(0,W-2*material_thickness)
        strip_edge=c.get('edge_struct',construction_cfg['structure_edge_enabled'])*strip_length/1000
        for i in range(strip_qty):
            add(f'Haut strip {i+1}',q,strip_length,strip_w,m,strip_edge)
    else:
        top_width=max(0,W-2*material_thickness)
        add('Haut',q,top_width,D,m,te)
    if c.get('bottom', bool(custom_cfg['bottom'])): add('Bas',q,W,D,m,te)
    if c.get('shelves',0): add('Étagère',c['shelves']*q,W-construction_cfg['shelf_width_reduction_mm'],D-construction_cfg['shelf_depth_reduction_mm'],m,c.get('edge_shelf',construction_cfg['shelf_edge_enabled'])*(W+D)/1000)
    if c.get('back_enabled', bool(custom_cfg['back_enabled'])):
        back_w=W-construction_cfg['back_width_reduction_mm']
        back_h=H-construction_cfg['back_height_reduction_mm']
        back_mat_cfg=materials.get(back,{})
        back_sw=float(back_mat_cfg.get('sheet_w',0) or 0)
        back_sh=float(back_mat_cfg.get('sheet_h',0) or 0)
        split_back=int(c.get('split_back', 0) or 0)

        # Back panels are the only pieces allowed to split.
        # If the full back fits in either orientation, keep it as one panel.
        fits_one = bool(back_sw and back_sh and (
            (back_w <= back_sw and back_h <= back_sh) or
            (back_w <= back_sh and back_h <= back_sw)
        ))

        if fits_one:
            add('Fond',q,back_w,back_h,back,0,'Fond')
        elif split_back == 2 and back_sw and back_sh:
            # Split into exactly two equal panels along the dimension that
            # prevents the original back from fitting.
            candidates=[]
            half_w=back_w/2.0
            half_h=back_h/2.0
            candidates.append((half_w, back_h, 'width'))
            candidates.append((back_w, half_h, 'height'))

            chosen=None
            for pw,ph,axis in candidates:
                if ((pw <= back_sw and ph <= back_sh) or
                    (pw <= back_sh and ph <= back_sw)):
                    chosen=(pw,ph,axis)
                    break

            if chosen is None:
                raise ValueError(
                    f'Back panel {back_w:.0f}x{back_h:.0f} mm cannot be split into exactly 2 panels that fit the {back_sw:.0f}x{back_sh:.0f} mm back sheet.'
                )

            pw,ph,axis=chosen
            add('Fond 1',q,pw,ph,back,0,'Fond')
            add('Fond 2',q,pw,ph,back,0,'Fond')
        else:
            raise ValueError(
                f'Back panel {back_w:.0f}x{back_h:.0f} mm does not fit the {back_sw:.0f}x{back_sh:.0f} mm back sheet and split_back is not set to 2.'
            )

    # Facade pieces must also be sent to nesting. They use the facade
    # material (or Glass for glass doors) and are never split.
    if c.get('doors', 0):
        dw=(W-(c['doors']-1)*construction_cfg['door_spacing_mm'])/c['doors']-construction_cfg['door_width_reduction_mm']
        dh=H-construction_cfg['door_height_reduction_mm']
        glass=c.get('door_type')=='Glass'
        mat='Glass' if glass else f
        add('Door',c['doors']*q,dw,dh,mat,
            0 if glass else c.get('edge_door',construction_cfg['door_edge_enabled'])*2*(dw+dh)/1000,
            'Door',glass)

    if c.get('drawers', 0):
        drawer_qty=int(c.get('drawers', 0))*q
        fh=min(
            construction_cfg['drawer_front_max_height_mm'],
            (H-construction_cfg['drawer_height_clearance_mm'])/c['drawers']
        )
        drawer_w=W-construction_cfg['drawer_width_reduction_mm']-construction_cfg['drawer_front_width_reduction_mm']
        drawer_h=fh-construction_cfg['drawer_front_height_reduction_mm']
        add('Front de drawer',drawer_qty,drawer_w,drawer_h,f,
            c.get('edge_door',construction_cfg['door_edge_enabled'])*2*(drawer_w+drawer_h)/1000,
            'Door')

        # Drawer box: real 5-piece interior, generated from JSON dimensions.
        # External box dimensions: width = cabinet width - configured reduction,
        # height/depth are configured in JSON. Material is the cabinet structure material.
        box_w=max(0,W-construction_cfg.get('drawer_box_width_reduction_mm',5))
        box_h=max(0,construction_cfg.get('drawer_box_height_mm',150))
        box_d=max(0,construction_cfg.get('drawer_box_depth_mm',450))
        t=max(0,material_thickness)
        # Two side panels, front/back panels, and one bottom panel.
        # The bottom sits between the four vertical panels.
        side_h=max(0,box_h-t)
        side_d=box_d
        front_back_w=max(0,box_w-2*t)
        front_back_h=side_h
        bottom_w=front_back_w
        bottom_d=max(0,box_d-t)
        add('Drawer box side',drawer_qty,side_d,side_h,m,0,'Drawer Box')
        add('Drawer box side',drawer_qty,side_d,side_h,m,0,'Drawer Box')
        add('Drawer box front/back',drawer_qty*2,front_back_w,front_back_h,m,0,'Drawer Box')
        add('Drawer box bottom',drawer_qty,bottom_w,bottom_d,m,0,'Drawer Box')

    return out

# --- Expand quantity rows into individual nesting pieces ---
def expand(rows):
    return [{**r,'piece':f"{r['name']} #{i+1}"} for r in rows for i in range(r['qty'])]

# --- CNC nesting: mixed parts, 0°/90° rotation only ---
def nest(pieces, gap, iterations):
    """CNC nesting using a true free-rectangle (MaxRects-style) packer.

    The optimizer's main objective is material usage: minimize sheet count,
    then minimize unused material and fragmentation. Every placement is tested
    against the current free rectangles at 0/90 degrees, so narrow pieces can
    fill vertical/horizontal leftover spaces created by larger panels.
    """
    import random

    gap=max(0.0,float(gap))
    iterations=max(1,int(iterations))
    rotation_angles=tuple(nesting_cfg['rotation_angles'])

    groups={}
    for p in pieces:
        if not p['glass']:
            groups.setdefault(p['material'],[]).append(p)

    def overlaps(a,b):
        ax,ay,aw,ah=a
        bx,by,bw,bh=b
        return not (
            ax+aw+gap <= bx or bx+bw+gap <= ax or
            ay+ah+gap <= by or by+bh+gap <= ay
        )

    def contained(a,b):
        ax,ay,aw,ah=a
        bx,by,bw,bh=b
        return ax >= bx-1e-9 and ay >= by-1e-9 and ax+aw <= bx+bw+1e-9 and ay+ah <= by+bh+1e-9

    def prune_free_rects(rects):
        # Remove duplicates and rectangles fully contained by another free area.
        unique=[]
        seen=set()
        for r in rects:
            x,y,w,h=r
            if w <= 1e-6 or h <= 1e-6:
                continue
            key=(round(x,6),round(y,6),round(w,6),round(h,6))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        kept=[]
        for i,r in enumerate(unique):
            if any(i != j and contained(r,o) for j,o in enumerate(unique)):
                continue
            kept.append(r)
        return kept

    def split_free_rects(free_rects, placed):
        px,py,pw,ph=placed
        out=[]
        for fx,fy,fw,fh in free_rects:
            if px >= fx+fw or px+pw <= fx or py >= fy+fh or py+ph <= fy:
                out.append((fx,fy,fw,fh))
                continue

            # Standard MaxRects split: retain every rectangular region around
            # the placed rectangle. The placed rectangle includes the required
            # CNC gap on its right/bottom edges.
            if px > fx:
                out.append((fx,fy,px-fx,fh))
            if px+pw < fx+fw:
                out.append((px+pw,fy,fx+fw-(px+pw),fh))
            if py > fy:
                out.append((fx,fy,fw,py-fy))
            if py+ph < fy+fh:
                out.append((fx,py+ph,fw,fy+fh-(py+ph)))
        return prune_free_rects(out)

    def candidate_score(free_rect, w, h, placements, sw, sh, tie):
        fx,fy,fw,fh=free_rect
        dw=fw-w
        dh=fh-h
        short_fit=min(dw,dh)
        long_fit=max(dw,dh)
        leftover=fw*fh-w*h
        # Prefer a tight fit first. Then prefer placements that leave compact,
        # usable rectangles instead of thin fragmented slivers.
        edge_bonus=(abs(fx) < 1e-6)+(abs(fy) < 1e-6)+(abs((fx+fw)-sw) < 1e-6)+(abs((fy+fh)-sh) < 1e-6)
        score=(short_fit,long_fit,leftover,-edge_bonus,tie)
        return score

    def place_best(remaining, free_rects, placements, sw, sh, rng, strategy):
        best=None
        # Evaluate every remaining piece against every free rectangle. This is
        # the important difference from the old edge/corner candidate method.
        for pi,p in enumerate(remaining):
            for angle in rotation_angles:
                w,h=(float(p['w']),float(p['h'])) if angle==0 else (float(p['h']),float(p['w']))
                for ri,fr in enumerate(free_rects):
                    fx,fy,fw,fh=fr
                    if w+gap > fw+1e-6 or h+gap > fh+1e-6:
                        continue
                    # Reserve the CNC gap to the right/bottom of the part.
                    rw=w+gap if fx+w+gap < sw else w
                    rh=h+gap if fy+h+gap < sh else h
                    if rw > fw+1e-6 or rh > fh+1e-6:
                        continue
                    tie=rng.random()*1e-3
                    base=candidate_score(fr,w+gap if fx+w+gap < sw else w,h+gap if fy+h+gap < sh else h,placements,sw,sh,tie)
                    # Different starts bias only tie-breaking/secondary fit,
                    # while primary objectives remain material utilization.
                    if strategy==1:
                        score=(base[0],base[2],base[1],base[3],base[4])
                    elif strategy==2:
                        score=(base[1],base[0],base[2],base[3],base[4])
                    else:
                        score=base
                    cand=(score,pi,ri,fx,fy,w,h,angle,rw,rh)
                    if best is None or cand[0] < best[0]:
                        best=cand
        return best

    def solve(mat, items, seed):
        sw=float(materials[mat]['sheet_w'])
        sh=float(materials[mat]['sheet_h'])
        rng=random.Random(seed)
        remaining=list(items)
        sheets=[]
        strategy=seed % 3

        while remaining:
            free_rects=[(0.0,0.0,sw,sh)]
            placements=[]

            while remaining:
                best=place_best(remaining,free_rects,placements,sw,sh,rng,strategy)
                if best is None:
                    break
                _,pi,ri,x,y,w,h,angle,rw,rh=best
                p=remaining.pop(pi)
                placements.append((p['piece'],x,y,w,h,angle))
                free_rects=split_free_rects(free_rects,(x,y,rw,rh))

            if not placements:
                p=remaining.pop(0)
                placed=False
                for angle in rotation_angles:
                    w,h=(float(p['w']),float(p['h'])) if angle==0 else (float(p['h']),float(p['w']))
                    if w <= sw+1e-6 and h <= sh+1e-6:
                        placements=[(p['piece'],0.0,0.0,w,h,angle)]
                        placed=True
                        break
                if not placed:
                    raise ValueError(f"Piece too large: {p['name']} {p['w']}x{p['h']} mm")

            sheets.append({'material':mat,'placements':placements})

        return sheets

    def solution_score(layout):
        total_waste=0.0
        fragmentation=0.0
        min_utilization=1.0
        for sh in layout:
            mat=sh['material']
            sw=float(materials[mat]['sheet_w'])
            shh=float(materials[mat]['sheet_h'])
            sheet_area=sw*shh
            used=sum(w*h for _,x,y,w,h,*_ in sh['placements'])
            total_waste += sheet_area-used
            min_utilization=min(min_utilization, used/sheet_area if sheet_area else 0)
            if sh['placements']:
                xs=[x+w for _,x,y,w,h,*_ in sh['placements']]
                ys=[y+h for _,x,y,w,h,*_ in sh['placements']]
                fragmentation += max(0.0,(max(xs)*max(ys))-used)
        return (len(layout),total_waste,fragmentation,-min_utilization)

    all_sheets=[]
    for mat,items in groups.items():
        best=None
        # Multiple starts remain supported, but each start now uses the free-
        # rectangle optimizer rather than the old edge-point greedy method.
        for i in range(iterations):
            seed=(i*1009 + len(items)*37 + len(mat)*101)
            layout=solve(mat,items,seed)
            score=solution_score(layout)
            if best is None or score < best[0]:
                best=(score,layout)
        all_sheets.extend(best[1])

    # Final collision/boundary validation.
    for sh in all_sheets:
        placements=sh['placements']
        mat=sh['material']
        sw=float(materials[mat]['sheet_w'])
        shh=float(materials[mat]['sheet_h'])
        for i,a in enumerate(placements):
            if a[1]+a[3] > sw+1e-6 or a[2]+a[4] > shh+1e-6:
                raise RuntimeError(f"Nesting boundary error on {mat}: {a[0]}")
            ar=(a[1],a[2],a[3],a[4])
            for b in placements[i+1:]:
                br=(b[1],b[2],b[3],b[4])
                if overlaps(ar,br):
                    raise RuntimeError(f"Nesting collision detected on {mat}: {a[0]} overlaps {b[0]}")

    return all_sheets


# --- Visual nesting: pre-render every sheet once ---
def render_sheet_png(s, i):
    mat=s['material']
    sw,sh=materials[mat]['sheet_w'],materials[mat]['sheet_h']
    fig,ax=plt.subplots(figsize=(11,6))
    ax.add_patch(Rectangle((0,0),sw,sh,fill=False,linewidth=2))
    material_color = materials.get(mat, {}).get('color', '#95A5A6')
    for name,x,y,w,h,*angle_data in s['placements']:
        ax.add_patch(Rectangle((x,y),w,h,facecolor=material_color,edgecolor='black',linewidth=0.8,alpha=0.72))
        ax.text(x+w/2,y+h/2,f'{name}\n{w}×{h}',ha='center',va='center',fontsize=7,color='black')
    ax.set(xlim=(0,sw),ylim=(0,sh),aspect='equal',title=f'Panel {i} — {mat} — {sw}×{sh} mm')
    ax.invert_yaxis()
    ax.margins(0)
    buf=io.BytesIO()
    fig.savefig(buf,format='png',bbox_inches='tight',dpi=120)
    plt.close(fig)
    return buf.getvalue()

def prepare_nesting_images(sheets):
    payload=json.dumps(sheets,sort_keys=True,default=str)
    colors={m:materials[m].get('color') for m in sorted(materials)}
    key=hashlib.sha256((payload+json.dumps(colors,sort_keys=True)).encode()).hexdigest()
    if st.session_state.get('nesting_image_key') != key:
        st.session_state['nesting_images']=[render_sheet_png(sh,i+1) for i,sh in enumerate(sheets)]
        st.session_state['nesting_image_key']=key
    return st.session_state['nesting_images']


def hinge_count(h):
    a,b,c=st.session_state.hinge_limits
    counts=hardware_cfg['hinge_counts']
    return counts[0] if h<=a else counts[1] if h<=b else counts[2] if h<=c else counts[3]

# -----------------------------------------------------------------------------
# UI workflow: Project Builder -> Bulk Edit -> Calculation & Results -> Report
# -----------------------------------------------------------------------------

def calculate_project():
    if not st.session_state.project:
        return None

    cfg = st.session_state.cfg
    base_linear_m=sum(
        float(x['cabinet'].get('width',0))*float(x.get('qty',1))/1000
        for x in st.session_state.project
        if 'Kitchen base cabinet' in str(x.get('name',''))
    )

    rows=sum((make_parts({**x['cabinet'],'name':x['name']},x['qty']) for x in st.session_state.project),[])
    pieces=expand(rows)

    gola_m = sum(
        float(x.get("cabinet", {}).get("width", 0)) * float(x.get("qty", 1)) / 1000
        * int(x.get("cabinet", {}).get("gola", 0))
        for x in st.session_state.project
    )
    plinthe_m = sum(
        float(x.get("cabinet", {}).get("width", 0)) * float(x.get("qty", 1)) / 1000
        * int(x.get("cabinet", {}).get("plinthe", 0))
        for x in st.session_state.project
    )
    st.session_state["gola_m"] = round(gola_m, 3)
    st.session_state["plinthe_m"] = round(plinthe_m, 3)

    gola_bars = math.ceil(gola_m / GOLA_BAR_LENGTH) if gola_m > 0 else 0
    plinthe_bars = math.ceil(plinthe_m / PLINTH_BAR_LENGTH) if plinthe_m > 0 else 0
    gola_cost = gola_bars * GOLA_BAR_PRICE
    plinthe_cost = plinthe_bars * PLINTH_BAR_PRICE

    gap=st.session_state.get('project_nesting_gap', nesting_cfg['gap_mm'])
    iterations=st.session_state.get('nesting_iterations', nesting_cfg['iterations'])
    sheets=nest(pieces,gap,iterations)
    prepare_nesting_images(sheets)

    counts={}
    for sh in sheets:
        counts[sh['material']]=counts.get(sh['material'],0)+1
    sheet_cost=sum(n*materials[m]['price'] for m,n in counts.items())
    glass_area=sum(r['w']*r['h']*r['qty']/1e6 for r in rows if r['glass'])
    glass_cost=glass_area*materials['Glass']['price_m2']

    struct_m=sum(front_edge_length(r)*r['qty']/1000 for r in rows if r.get('edge_type')=='Structure')
    door_m=sum((2*float(r.get('w',0))+2*float(r.get('h',0)))*r['qty']/1000 for r in rows if r.get('edge_type')=='Porte' or is_front_visible_part(r.get('name','')))
    sr=math.ceil(struct_m/150) if struct_m else 0
    dr=math.ceil(door_m/150) if door_m else 0
    edge_cost=sr*edges['Structure Edge Band']['roll_price']+dr*edges['Door Edge Band']['roll_price']

    hb=st.session_state.get('project_hinge_brand',list(hardware['Hinge'])[0])
    ab=st.session_state.get('project_amortisseur_brand',list(hardware['Amortisseur'])[0])
    rb=st.session_state.get('project_drawer_runner_brand',list(hardware['Drawer runner'])[0])
    gb=st.session_state.get('project_hanging_box_brand',list(hardware['Hanging box'])[0])
    hq=aq=dq=gq=legs_q=0
    for x in st.session_state.project:
        c=x['cabinet']; qty=x['qty']
        hq+=c.get('doors',0)*qty*hinge_count(c['height'])
        aq+=c.get('doors',0)*qty*c.get('amortisseurs_per_door',1)
        dq+=c.get('drawers',0)*qty
        gq+=c.get('hanging_boxes',0)*qty
        legs_q+=int(c.get('legs',0))*qty
    hw=hq*hardware['Hinge'][hb]+aq*hardware['Amortisseur'][ab]+dq*hardware['Drawer runner'][rb]+gq*hardware['Hanging box'][gb]
    legs_cost=legs_q*accessories['Leg']['price']

    acc=cfg['screws']*accessories['Screw box']['price']+cfg['bits']*accessories['Router bit']['price']
    led_al_m=float(cfg.get('led_al_m', 2.0))
    led=led_al_m*production_cfg['led_strip_price_per_m']+math.ceil(led_al_m/production_cfg['aluminium_bar_length_m'])*production_cfg['aluminium_bar_price'] if led_al_m>0 else 0.0
    operating=cfg['hand']+cfg['days']*cfg['rent']+cfg['install']+cfg['transport']
    cogs=sheet_cost+glass_cost+edge_cost+hw+acc+legs_cost+led+gola_cost+plinthe_cost+operating

    width_m=sum(float(x['cabinet'].get('width',0))*float(x.get('qty',1))*float(x['cabinet'].get('linear_meter_multiplier',1))/1000 for x in st.session_state.project)
    volume_m3=sum(
        float(x['cabinet'].get('width',0))*float(x['cabinet'].get('height',0))*float(x['cabinet'].get('depth',0))*float(x.get('qty',1))/1e9
        for x in st.session_state.project if x['cabinet'].get('type') != '2d_panel' and x['cabinet'].get('depth') is not None
    )
    strategy=st.session_state.get('margin_strategy','Margin per linear meter')
    if strategy=='Margin per linear meter':
        margin=width_m*cfg['margin_m']
        margin_label=f"{cfg['margin_m']:,.0f} DA / linear meter"
        final=cogs+margin
    elif strategy=='Margin per cubic meter':
        margin=volume_m3*cfg['margin_m3']
        margin_label=f"{cfg['margin_m3']:,.0f} DA / m³"
        final=cogs+margin
    elif strategy=='Fixed margin':
        margin=cfg['fixed']
        margin_label='Fixed project margin'
        final=cogs+margin
    else:
        # X3: multiply all costs except rent, transport and manufacturing/hand labor.
        x3_base=cogs-(cfg['hand']+cfg['days']*cfg['rent']+cfg['transport'])
        x3_base=max(0.0,x3_base)
        final=x3_base*3
        margin=final-cogs
        margin_label=f"X3 — eligible costs × 3 ({x3_base:,.0f} DA base)"
    margin_pct=(margin/final*100) if final else 0

    # Detailed per-sheet COGS and utilization. Each physical sheet is shown
    # individually so the operator can see exactly how efficiently it was used.
    sheet_rows=[]
    for sheet_no, sh in enumerate(sheets, start=1):
        material=sh['material']
        unit_price=float(materials[material].get('price',0))
        sw=float(materials[material].get('sheet_w',0) or 0)
        shh=float(materials[material].get('sheet_h',0) or 0)
        sheet_area=sw*shh
        used_area=sum(float(pl[3])*float(pl[4]) for pl in sh.get('placements',[]))
        utilization=(used_area/sheet_area*100) if sheet_area else 0.0
        sheet_rows.append({
            'Sheet':'Sheet %d' % sheet_no,
            'Sheet type':material,
            'Quantity':1,
            'Unit price (DA)':round(unit_price,2),
            'Sheet Utilisation (%)':round(utilization,1),
            'COGS (DA)':round(unit_price,2)
        })
    # Detailed Other COGS: every line is always shown, including zero quantities/costs.
    cogs_rows=[
        {'COGS item':'Structure Edge Band','Quantity':sr,'Unit price (DA)':edges['Structure Edge Band']['roll_price'],'COGS (DA)':round(sr*edges['Structure Edge Band']['roll_price'],2)},
        {'COGS item':'Door Edge Band','Quantity':dr,'Unit price (DA)':edges['Door Edge Band']['roll_price'],'COGS (DA)':round(dr*edges['Door Edge Band']['roll_price'],2)},
        {'COGS item':f'Gola bar ({GOLA_BAR_LENGTH:g}m)','Quantity':gola_bars,'Unit price (DA)':GOLA_BAR_PRICE,'COGS (DA)':round(gola_cost,2)},
        {'COGS item':f'Plinth bar ({PLINTH_BAR_LENGTH:g}m)','Quantity':plinthe_bars,'Unit price (DA)':PLINTH_BAR_PRICE,'COGS (DA)':round(plinthe_cost,2)},
        {'COGS item':f'Hinges — {hb}','Quantity':hq,'Unit price (DA)':hardware['Hinge'][hb],'COGS (DA)':round(hq*hardware['Hinge'][hb],2)},
        {'COGS item':f'Soft-close — {ab}','Quantity':aq,'Unit price (DA)':hardware['Amortisseur'][ab],'COGS (DA)':round(aq*hardware['Amortisseur'][ab],2)},
        {'COGS item':f'Drawer runners — {rb}','Quantity':dq,'Unit price (DA)':hardware['Drawer runner'][rb],'COGS (DA)':round(dq*hardware['Drawer runner'][rb],2)},
        {'COGS item':f'Lift mechanisms — {gb}','Quantity':gq,'Unit price (DA)':hardware['Hanging box'][gb],'COGS (DA)':round(gq*hardware['Hanging box'][gb],2)},
        {'COGS item':'Screw boxes','Quantity':cfg['screws'],'Unit price (DA)':accessories['Screw box']['price'],'COGS (DA)':round(cfg['screws']*accessories['Screw box']['price'],2)},
        {'COGS item':'Router bits','Quantity':cfg['bits'],'Unit price (DA)':accessories['Router bit']['price'],'COGS (DA)':round(cfg['bits']*accessories['Router bit']['price'],2)},
        {'COGS item':'Legs','Quantity':legs_q,'Unit price (DA)':accessories['Leg']['price'],'COGS (DA)':round(legs_cost,2)},
        {'COGS item':'LED strip','Quantity':led_al_m,'Unit price (DA)':production_cfg['led_strip_price_per_m'],'COGS (DA)':round(led_al_m*production_cfg['led_strip_price_per_m'],2)},
        {'COGS item':f'Aluminium bars ({production_cfg["aluminium_bar_length_m"]}m)','Quantity':math.ceil(led_al_m/production_cfg['aluminium_bar_length_m']) if led_al_m>0 else 0,'Unit price (DA)':production_cfg['aluminium_bar_price'],'COGS (DA)':round((math.ceil(led_al_m/production_cfg['aluminium_bar_length_m']) if led_al_m>0 else 0)*production_cfg['aluminium_bar_price'],2)},
        {'COGS item':'Manufacturing labor','Quantity':cfg['days'],'Unit price (DA)':cfg['hand'],'COGS (DA)':round(cfg['hand'],2)},
        {'COGS item':'Rent','Quantity':cfg['days'],'Unit price (DA)':cfg['rent'],'COGS (DA)':round(cfg['days']*cfg['rent'],2)},
        {'COGS item':'Client installation','Quantity':1,'Unit price (DA)':cfg['install'],'COGS (DA)':round(cfg['install'],2)},
        {'COGS item':'Transport','Quantity':1,'Unit price (DA)':cfg['transport'],'COGS (DA)':round(cfg['transport'],2)},
        {'COGS item':'Glass','Quantity':glass_area,'Unit price (DA)':materials['Glass']['price_m2'],'COGS (DA)':round(glass_cost,2)},
    ]

    result={
        'rows':rows,'pieces':pieces,'sheets':sheets,'counts':counts,'sheet_rows':sheet_rows,'cogs_rows':cogs_rows,
        'sheet_cost':sheet_cost,'glass_area':glass_area,'glass_cost':glass_cost,'struct_m':struct_m,'door_m':door_m,
        'structure_rolls':sr,'door_rolls':dr,'edge_cost':edge_cost,'gola_m':gola_m,'plinthe_m':plinthe_m,
        'gola_bars':gola_bars,'plinthe_bars':plinthe_bars,'gola_cost':gola_cost,'plinthe_cost':plinthe_cost,
        'hinge_brand':hb,'amortisseur_brand':ab,'drawer_runner_brand':rb,'hanging_box_brand':gb,
        'hinges':hq,'amortisseurs':aq,'drawer_runners':dq,'hanging_boxes':gq,'hardware_cost':hw,
        'accessories_cost':acc,'legs':legs_q,'legs_cost':legs_cost,'led_cost':led,'operating_cost':operating,'cogs':cogs,'linear_m':width_m,'volume_m3':volume_m3,
        'margin':margin,'margin_label':margin_label,'selling_price':final,'margin_pct':margin_pct,
        'iterations':iterations,'gap':gap,
    }
    st.session_state.project_result=result
    return result


def build_project_pdf(result, include_financials=True):
    buffer=io.BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=14*mm,leftMargin=14*mm,topMargin=14*mm,bottomMargin=14*mm)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Small',parent=styles['BodyText'],fontSize=8,leading=10))
    styles.add(ParagraphStyle(name='Section',parent=styles['Heading2'],spaceBefore=8,spaceAfter=5))
    story=[Paragraph('AL MOUDIR MOBILIER — PROJECT REPORT',styles['Title']),Spacer(1,4)]
    info=[
        ['Project',st.session_state.get('project_name') or '—'],
        ['Client',st.session_state.get('client_name') or '—'],
        ['Reference',st.session_state.get('project_reference') or '—'],
        ['Notes',st.session_state.get('project_notes') or '—'],
    ]
    t=Table(info,colWidths=[35*mm,145*mm]); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,colors.grey),('BACKGROUND',(0,0),(0,-1),colors.whitesmoke),('VALIGN',(0,0),(-1,-1),'TOP')])); story += [t]
    story += [Paragraph('1. Project Cabinets',styles['Section'])]
    cab=[['Cabinet','Qty','W (mm)','H (mm)','D (mm)','Material','Facade','Back','Gola','Plinth']]
    for x in st.session_state.project:
        c=x['cabinet']; cab.append([x['name'],x['qty'],c.get('width',''),c.get('height',''),c.get('depth',''),c.get('material',''),c.get('facade',''),c.get('back',''),c.get('gola',0),c.get('plinthe',0)])
    t=Table(cab,colWidths=[31*mm,10*mm,11*mm,11*mm,11*mm,23*mm,23*mm,23*mm,9*mm,9*mm],repeatRows=1); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('FONTSIZE',(0,0),(-1,-1),6.5),('VALIGN',(0,0),(-1,-1),'TOP')])); story += [t]
    story += [Paragraph('2. Hardware',styles['Section']),Paragraph(f"Hinge: {result['hinge_brand']} — {result['hinges']} pcs",styles['Small']),Paragraph(f"Soft-close: {result['amortisseur_brand']} — {result['amortisseurs']} pcs",styles['Small']),Paragraph(f"Drawer runner: {result['drawer_runner_brand']} — {result['drawer_runners']} pcs",styles['Small']),Paragraph(f"Lift mechanism: {result['hanging_box_brand']} — {result['hanging_boxes']} pcs",styles['Small'])]
    if include_financials:
            story += [Paragraph('3. Material / Sheet COGS',styles['Section'])]
            sr=[['Sheet','Sheet type','Qty','Unit price (DA)','Utilisation','COGS (DA)']]+[[r['Sheet'],r['Sheet type'],r['Quantity'],f"{r['Unit price (DA)']:,.0f}",f"{r['Sheet Utilisation (%)']:.1f}%",f"{r['COGS (DA)']:,.0f}"] for r in result['sheet_rows']]
            t=Table(sr,colWidths=[15*mm,50*mm,15*mm,32*mm,28*mm,35*mm],repeatRows=1); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('ALIGN',(2,1),(-1,-1),'RIGHT'),('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'MIDDLE')])); story += [t]
            story += [Paragraph('4. Other COGS',styles['Section'])]
            cr=[['Item','Qty','Unit price (DA)','COGS (DA)']]+[[r['COGS item'],r['Quantity'],r['Unit price (DA)'] if r['Unit price (DA)']!='' else '—',f"{r['COGS (DA)']:,.0f}"] for r in result['cogs_rows']]
            t=Table(cr,colWidths=[80*mm,20*mm,35*mm,35*mm],repeatRows=1); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('ALIGN',(1,1),(-1,-1),'RIGHT'),('FONTSIZE',(0,0),(-1,-1),8)])); story += [t]
            story += [Paragraph('5. Production / Nesting Details',styles['Section']),Paragraph(f"Nesting gap: {result['gap']} mm · Optimization iterations: {result['iterations']}",styles['Small']),Paragraph(f"Gola: {result['gola_m']:.2f} m / {result['gola_bars']} bar(s) · Plinth: {result['plinthe_m']:.2f} m / {result['plinthe_bars']} bar(s)",styles['Small']),Paragraph(f"Glass area: {result['glass_area']:.2f} m²",styles['Small']),Paragraph(f"Nesting sheets: {len(result['sheets'])}",styles['Small'])]
            story += [Paragraph('6. Final Financial Summary',styles['Section'])]
            fin=[['Linear meters',f"{result['linear_m']:.2f} m"],['Total COGS',f"{result['cogs']:,.0f} DA"],['Margin',f"{result['margin']:,.0f} DA"],['Margin strategy',result['margin_label']],['Margin %',f"{result['margin_pct']:.1f}%"],['SELLING PRICE',f"{result['selling_price']:,.0f} DA"]]
            t=Table(fin,colWidths=[60*mm,110*mm]); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,colors.grey),('BACKGROUND',(0,0),(0,-1),colors.whitesmoke),('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9)])); story += [t]
    else:
        story += [Paragraph('2. Final Client Price',styles['Section'])]
        fin=[['FINAL CLIENT PRICE',f"{result['selling_price']:,.0f} DA"]]
        t=Table(fin,colWidths=[60*mm,110*mm]); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.5,colors.grey),('BACKGROUND',(0,0),(0,0),colors.whitesmoke),('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),11)])); story += [t]
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# All users get the same main workflow pages. Financial/internal details are role-filtered inside those pages.
# User Management is a separate administrator-only tab.
if IS_ADMIN:
    t1, t2, t3, t4, t5 = st.tabs([
        '1. Project Builder',
        '2. Bulk Edit',
        '3. Calculation & Results',
        '4. Final Report',
        '5. User Management'
    ])
else:
    t1, t2, t3, t4 = st.tabs([
        '1. Project Builder',
        '2. Bulk Edit',
        '3. Calculation & Results',
        '4. Final Report'
    ])
    t5 = None
cfg=st.session_state.cfg

with t1:
    st.header('Project Builder')
    st.subheader('Project information')
    a,b,c=st.columns(3)
    st.session_state.project_name=a.text_input('Project name',st.session_state.get('project_name',''))
    st.session_state.client_name=b.text_input('Client name',st.session_state.get('client_name',''))
    st.session_state.project_reference=c.text_input('Project reference',st.session_state.get('project_reference',''))
    st.session_state.project_notes=st.text_area('Project notes',st.session_state.get('project_notes',''),height=70)

    st.divider()
    st.subheader('1. Add cabinets')
    names=list(presets)+['Custom cabinet']
    a,b=st.columns([4,1]); sel=a.selectbox('Cabinet model',names); q=b.number_input('Quantity',1,999,1)
    if sel=='Custom cabinet':
        a,b,c=st.columns(3); W=a.number_input('Width (mm)',100,5000,int(custom_cfg['width'])); H=b.number_input('Height (mm)',100,5000,int(custom_cfg['height'])); D=c.number_input('Depth (mm)',100,1500,int(custom_cfg['depth']))
        a,b,c=st.columns(3); m=a.selectbox('Structure material',list(materials)[:-1]); f=b.selectbox('Facade material',list(materials)[:-1]); bk=c.selectbox('Back material',list(materials)[:-1])
        a,b,c=st.columns(3); doors=a.number_input('Doors',0,20,int(custom_cfg['doors'])); shelves=b.number_input('Shelves',0,50,int(custom_cfg['shelves'])); drawers=c.number_input('Drawers',0,20,int(custom_cfg['drawers']))
        dtype=st.radio('Door type',['Panneau','Glass'],horizontal=True)
        cdata={'width':W,'height':H,'depth':D,'material':m,'facade':f,'back':bk,'doors':doors,'shelves':shelves,'drawers':drawers,'door_type':dtype,'bottom':bool(custom_cfg['bottom']),'back_enabled':bool(custom_cfg['back_enabled']),'gola':0,'plinthe':0}
    else:
        cdata=dict(presets[sel])
        if cdata.get('type') == '2d_panel':
            a,b=st.columns(2)
            cdata['width']=a.number_input('Width (mm)',100,5000,int(cdata['width']),step=10,key=f'preset_width_{sel}')
            cdata['height']=b.number_input('Height (mm)',100,5000,int(cdata['height']),step=10,key=f'preset_height_{sel}')
            mats=list(materials)[:-1]
            cdata['facade']=st.selectbox('Facade material',mats,index=mats.index(cdata.get('facade')) if cdata.get('facade') in mats else 0,key=f'facade_{sel}')
            cdata['material']=cdata['facade']
        else:
            a,b,c=st.columns(3)
            cdata['width']=a.number_input('Width (mm)',100,5000,int(cdata['width']),step=10,key=f'preset_width_{sel}')
            cdata['height']=b.number_input('Height (mm)',100,5000,int(cdata['height']),step=10,key=f'preset_height_{sel}')
            cdata['depth']=c.number_input('Depth (mm)',100,1500,int(cdata['depth']),step=10,key=f'preset_depth_{sel}')
            a,b=st.columns(2)
            mats=list(materials)[:-1]
            cdata['material']=a.selectbox('Structure material',mats,index=mats.index(cdata['material']) if cdata.get('material') in mats else 0,key=f'structure_{sel}')
            cdata['facade']=b.selectbox('Facade material',mats,index=mats.index(cdata['facade']) if cdata.get('facade') in mats else 0,key=f'facade_{sel}')
            dtype=st.radio('Door type',['Panneau','Glass'],index=1 if cdata.get('door_type')=='Glass' else 0,horizontal=True,key=f'dtype_{sel}')
            cdata['door_type']='Glass' if dtype=='Glass' else 'Panneau'
    if st.button('➕ Add cabinet',type='primary'): st.session_state.project.append({'name':sel,'qty':q,'cabinet':cdata}); st.success('Cabinet added.')

    st.divider()
    st.subheader('2. Current project')
    if st.session_state.project:
        for i,x in enumerate(st.session_state.project):
            a,b=st.columns([7,1])
            if x['cabinet'].get('type') == '2d_panel':
                a.write(f"**{x['name']}** — Qty {x['qty']} · {x['cabinet'].get('width',0)} × {x['cabinet'].get('height',0)} mm · 2D panel")
            else:
                a.write(f"**{x['name']}** — Qty {x['qty']} · {x['cabinet'].get('width',0)} × {x['cabinet'].get('height',0)} × {x['cabinet'].get('depth',0)} mm")
            if b.button('Remove',key=f'r{i}'): st.session_state.project.pop(i); st.rerun()
    else: st.info('No cabinets added yet.')

    if IS_ADMIN:
        st.divider()
        st.subheader('3. Project costs')
        a,b,c,d=st.columns(4)
        cfg['screws']=a.number_input('Screw boxes',0,100,cfg['screws']); cfg['bits']=b.number_input('Router bit CNCs',0,100,cfg['bits']); cfg['days']=c.number_input('Manufacturing days',0.,365.,cfg['days']); cfg['rent']=d.number_input('Rent / day',0,1000000,cfg['rent'])
        a,b,c=st.columns(3); cfg['hand']=a.number_input('Manufacturing labor',0,1000000,cfg['hand']); cfg['install']=b.number_input('Client installation',0,1000000,cfg['install']); cfg['transport']=c.number_input('Transport / project',0,1000000,cfg['transport'])
        cfg['led_al_m']=st.number_input('LED + Aluminium (m)',0.0,10000.0,float(cfg.get('led_al_m',2.0)),step=0.5)
        st.caption(f"LED = {production_cfg['led_strip_price_per_m']:,.0f} DA/m · Aluminium bar = {production_cfg['aluminium_bar_length_m']} m for {production_cfg['aluminium_bar_price']:,.0f} DA.")

        st.divider()
        st.subheader('4. Margin strategy')
        strategy=st.radio('Choose one strategy',['Margin per linear meter','Margin per cubic meter','Fixed margin','X3'],horizontal=True,key='margin_strategy')
        if strategy=='Margin per linear meter':
            cfg['margin_m']=st.number_input('Margin / linear meter',0,1000000,cfg['margin_m'])
        elif strategy=='Margin per cubic meter':
            cfg['margin_m3']=st.number_input('Margin / cubic meter',0,10000000,cfg['margin_m3'])
        elif strategy=='Fixed margin':
            cfg['fixed']=st.number_input('Fixed margin / project',0,10000000,cfg['fixed'])
        else:
            st.info('X3 = (all costs − rent − transport − manufacturing labor) × 3')

        st.divider()
        st.subheader('5. Calculation settings')
        with st.expander('Advanced calculation settings'):
            a,b,c=st.columns(3); a.number_input('Nesting gap (mm)',0,100,int(nesting_cfg['gap_mm']),key='project_nesting_gap'); b.number_input('Optimization iterations',int(nesting_cfg['min_iterations']),int(nesting_cfg['max_iterations']),int(nesting_cfg['iterations']),step=int(nesting_cfg['iteration_step']),key='nesting_iterations'); c.caption('Rotation: 0° / 90°')
            a,b,c=st.columns(3); st.session_state.hinge_limits[0]=a.number_input('2 hinges up to mm',100,3000,st.session_state.hinge_limits[0]); st.session_state.hinge_limits[1]=b.number_input('3 hinges up to mm',100,3000,st.session_state.hinge_limits[1]); st.session_state.hinge_limits[2]=c.number_input('4 hinges up to mm',100,4000,st.session_state.hinge_limits[2])
    else:
        st.divider()

with t2:
    st.header('Bulk Edit')
    st.divider()
    st.subheader('Global hardware brands')
    st.caption('These selections apply to the entire project.')
    a,b,c,d=st.columns(4)
    st.session_state['project_hinge_brand']=a.selectbox('Hinge brand',list(hardware['Hinge']),index=list(hardware['Hinge']).index(st.session_state.get('project_hinge_brand')) if st.session_state.get('project_hinge_brand') in hardware['Hinge'] else 0,key='prices_hinge_brand')
    st.session_state['project_amortisseur_brand']=b.selectbox('Soft-close brand',list(hardware['Amortisseur']),index=list(hardware['Amortisseur']).index(st.session_state.get('project_amortisseur_brand')) if st.session_state.get('project_amortisseur_brand') in hardware['Amortisseur'] else 0,key='prices_amortisseur_brand')
    st.session_state['project_drawer_runner_brand']=c.selectbox('Drawer runner brand',list(hardware['Drawer runner']),index=list(hardware['Drawer runner']).index(st.session_state.get('project_drawer_runner_brand')) if st.session_state.get('project_drawer_runner_brand') in hardware['Drawer runner'] else 0,key='prices_drawer_runner_brand')
    st.session_state['project_hanging_box_brand']=d.selectbox('Lift mechanism brand',list(hardware['Hanging box']),index=list(hardware['Hanging box']).index(st.session_state.get('project_hanging_box_brand')) if st.session_state.get('project_hanging_box_brand') in hardware['Hanging box'] else 0,key='prices_hanging_box_brand')
    st.divider()
    st.subheader('Global material changes')
    a,b=st.columns(2); gm=a.selectbox('New facade material',list(materials)[:-1],key='bulk_facade'); sm=b.selectbox('New structure material',list(materials)[:-1],key='bulk_structure')
    if st.button('Apply global facade change'): 
        for x in st.session_state.project:
            if x['cabinet'].get('door_type')!='Glass': x['cabinet']['facade']=gm
        st.success('Global facade material changed.')
    if st.button('Apply global structure change'):
        for x in st.session_state.project: x['cabinet']['material']=sm
        st.success('Global structure material changed.')
with t3:
    st.header('Calculation & Results')
    st.divider()
    if not st.session_state.project:
        st.info('Build the project first, then calculate it here.')
    else:
        st.write(f"**{st.session_state.get('project_name') or 'Unnamed project'}** · {len(st.session_state.project)} cabinet line(s)")
        if st.button('Calculate / Recalculate project',type='primary'):
            calculate_project()
        result=st.session_state.get('project_result')
        if result:
            # Users do not see internal financial metrics. Admins see the full financial row.
            if IS_ADMIN:
                a,b,c,d,e,f=st.columns(6)
                a.metric('Linear meters',f"{result['linear_m']:.2f} m")
                b.metric('Volume',f"{result['volume_m3']:.3f} m³")
                c.metric('COGS',f"{result['cogs']:,.0f} DA")
                d.metric('Selling price',f"{result['selling_price']:,.0f} DA")
                e.metric('Margin',f"{result['margin']:,.0f} DA")
                f.metric('Margin %',f"{result['margin_pct']:.1f}%")
                st.caption(f"Margin strategy: {result['margin_label']}")
            else:
                a,b,c=st.columns(3)
                a.metric('Linear meters',f"{result['linear_m']:.2f} m")
                b.metric('Volume',f"{result['volume_m3']:.3f} m³")
                c.metric('Selling price',f"{result['selling_price']:,.0f} DA")
            a,b,c,d=st.columns(4)
            a.metric('Gola',f"{result['gola_m']:.2f} m · {result['gola_bars']} bar(s)")
            b.metric('Plinth',f"{result['plinthe_m']:.2f} m · {result['plinthe_bars']} bar(s)")
            c.metric('Panels',sum(result['counts'].values()))
            d.metric('Cabinets',sum(int(x.get('qty',1)) for x in st.session_state.project))
            st.divider()
            st.subheader('Cabinets in project')
            st.dataframe(pd.DataFrame([{'Cabinet':x['name'],'Quantity':x['qty'],'Width (mm)':x['cabinet'].get('width',0),'Linear meters':float(x['cabinet'].get('width',0))*float(x.get('qty',1))*float(x.get('linear_meter_multiplier',1))/1000} for x in st.session_state.project]),use_container_width=True,hide_index=True)
            st.divider()
            st.subheader('Nesting / Sheet Details')
            sr=pd.DataFrame(result['sheet_rows']).copy()
            if not IS_ADMIN:
                sr=sr.drop(columns=[c for c in ['Unit price (DA)','COGS (DA)'] if c in sr.columns],errors='ignore')
            st.dataframe(sr,use_container_width=True,hide_index=True)
            st.divider()
            st.subheader('Other Cost Details')
            cr=pd.DataFrame(result['cogs_rows']).copy()
            if not IS_ADMIN:
                cr=cr.drop(columns=[c for c in ['Unit price (DA)','COGS (DA)'] if c in cr.columns],errors='ignore')
            st.dataframe(cr,use_container_width=True,hide_index=True)
            with st.expander('Visual nesting',expanded=False):
                sheets=result['sheets']
                if sheets:
                    st.session_state['nesting_carousel_index']=max(0,min(st.session_state.get('nesting_carousel_index',0),len(sheets)-1))
                    p1,p2,p3=st.columns([1,2,1])
                    if p1.button('← Previous',disabled=st.session_state['nesting_carousel_index']==0,key='nesting_previous_v53'):
                        st.session_state['nesting_carousel_index']-=1; st.rerun()
                    p2.markdown(f"**Sheet {st.session_state['nesting_carousel_index']+1} / {len(sheets)}**")
                    if p3.button('Next →',disabled=st.session_state['nesting_carousel_index']>=len(sheets)-1,key='nesting_next_v53'):
                        st.session_state['nesting_carousel_index']+=1; st.rerun()
                    st.image(st.session_state['nesting_images'][st.session_state['nesting_carousel_index']],use_container_width=True)

with t4:
    st.header('Final Project Report')
    st.divider()
    result=st.session_state.get('project_result')
    if not st.session_state.project:
        st.info('Build the project first.')
    elif not result:
        st.warning('Calculate the project first in Step 3.')
    else:
        if IS_ADMIN:
            st.success('Project calculation is ready. Generate the final PDF report below.')
            a,b,c,d=st.columns(4); a.metric('COGS',f"{result['cogs']:,.0f} DA"); b.metric('Selling price',f"{result['selling_price']:,.0f} DA"); c.metric('Margin',f"{result['margin']:,.0f} DA"); d.metric('Margin %',f"{result['margin_pct']:.1f}%")
        else:
            st.success(f"FINAL CLIENT PRICE: {result['selling_price']:,.0f} DA")
        if st.button('📄 Generate Project PDF',type='primary'):
            pdf_bytes=build_project_pdf(result, include_financials=IS_ADMIN)
            st.download_button('Download Project PDF',data=pdf_bytes,file_name=f"Al_Moudir_Project_{st.session_state.get('project_reference') or 'Report'}.pdf",mime='application/pdf')
        if IS_ADMIN:
            st.write('The report includes project information, cabinet details, hardware selections and quantities, sheet/material COGS, other COGS, Gola/Plinth, nesting settings, and the final financial summary.')

if IS_ADMIN:
    with t5:
        st.header('User Management')
        st.caption('Administrators can create users, disable accounts and reset passwords. Passwords are stored as salted PBKDF2 hashes.')
        users=_load_users()
        st.subheader('Existing users')
        table=[]
        for uname,u in users.items():
            table.append({
                'Username': uname,
                'Role': 'Administrator' if u.get('role')=='admin' else 'User',
                'Active': bool(u.get('active',True)),
                'Margin strategy': u.get('margin_strategy', _user_default_margin_strategy())
            })
        st.dataframe(pd.DataFrame(table),use_container_width=True,hide_index=True)
        st.divider()
        st.subheader('Create user')
        with st.form('create_user_form'):
            nu=st.text_input('Username',key='new_username')
            np=st.text_input('Password',type='password',key='new_password')
            nr=st.selectbox('Role',['user','admin'],key='new_role')
            create=st.form_submit_button('Create user',type='primary')
        if create:
            nu=nu.strip()
            if not nu or not np:
                st.error('Username and password are required.')
            elif nu in users:
                st.error('That username already exists.')
            else:
                salt,digest=_hash_password(np)
                users[nu]={
                    'password_hash': digest,
                    'salt': salt,
                    'role': nr,
                    'active': True,
                    'margin_strategy': _user_default_margin_strategy()
                }
                _save_users(users)
                st.success(f'User {nu} created.')
                st.rerun()
        st.divider()
        st.subheader('Manage user')
        candidates=[u for u in users if u != st.session_state.get('username')]
        if candidates:
            target=st.selectbox('User',candidates,key='manage_user')
            a,b=st.columns(2)
            new_role=a.selectbox('Role',['user','admin'],index=0 if users[target].get('role')=='user' else 1,key='manage_role')
            new_active=b.checkbox('Active',value=bool(users[target].get('active',True)),key='manage_active')
            strategy_options=USER_MARGIN_STRATEGIES
            current_strategy=users[target].get('margin_strategy', _user_default_margin_strategy())
            if current_strategy not in strategy_options:
                current_strategy=_user_default_margin_strategy()
            new_strategy=st.selectbox(
                'Margin strategy',
                strategy_options,
                index=strategy_options.index(current_strategy),
                key='manage_margin_strategy'
            )
            new_pw=st.text_input('New password (leave blank to keep current)',type='password',key='manage_password')
            if st.button('Save user changes',type='primary'):
                users[target]['role']=new_role
                users[target]['active']=new_active
                users[target]['margin_strategy']=new_strategy
                if new_pw:
                    salt,digest=_hash_password(new_pw)
                    users[target]['salt']=salt
                    users[target]['password_hash']=digest
                _save_users(users)
                st.success('User updated.')
                st.rerun()
        else:
            st.info('No other users to manage.')
