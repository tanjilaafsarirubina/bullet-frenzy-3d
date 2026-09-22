"""
Bullet Frenzy - a 3D arena shooter built with PyOpenGL and GLUT.

Hold your ground in a walled checkerboard arena while pulsing enemies close
in from every side. Turn, aim and shoot them down before they reach you.
Every enemy that touches you costs a life, and every bullet that flies out
of the arena counts as a miss. Run out of lives or miss too often and the
game is over.

Controls
    W / S         move forward / backward (along the gun's direction)
    A / D         rotate left / right
    Left click    fire
    Right click   toggle first-person / third-person camera
    Left / Right  orbit the camera around the player
    Up / Down     raise / lower the camera
    C             toggle cheat mode (gun spins and fires automatically)
    V             toggle cheat vision (first-person camera locks onto
                  the nearest enemy while cheat mode is on)
    R             restart

Run:
    python bullet_frenzy.py
"""

import math
import random
import time

from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *


# ---------------------------------------------------------------------------
# Settings
#
# Speeds are per second. BULLET_SPEED and ENEMY_SPEED match how the game
# originally played when it moved things a fixed amount per frame at ~300 FPS.
# ---------------------------------------------------------------------------

WINDOW_TITLE = b"Bullet Frenzy"
WINDOW_W, WINDOW_H = 1000, 800

# Arena: a square floor spanning [-ARENA, ARENA] on both axes.
ARENA  = 600
TILE   = 100
WALL_H = 150

# Player
START_LIVES   = 5
MOVE_STEP     = 25.0    # distance moved per W/S key press
TURN_STEP     = 30.0    # degrees turned per A/D key press
PLAYER_RADIUS = 22.0    # collision radius against enemies
EDGE_MARGIN   = 30.0    # closest the player may get to a wall

# Bullets
BULLET_SPEED  = 4500.0  # units per second
BULLET_HALF   = 8.0     # half the edge length of the bullet cube
MUZZLE_OFFSET = 38.0    # bullets spawn this far in front of the player
MUZZLE_HEIGHT = 55.0
MAX_MISSES    = 10

# Enemies
ENEMY_COUNT   = 5
ENEMY_SPEED   = 30.0    # units per second
ENEMY_BODY_R  = 28.0
ENEMY_HEAD_R  = 12.0
PULSE_SPEED   = 2.8     # radians per second of the grow/shrink cycle
SPAWN_MIN_GAP = 220.0   # enemies spawn at least this far from the player

# Smallest possible bullet-vs-enemy hit distance (enemy fully shrunk).
MIN_HIT_RADIUS = ENEMY_BODY_R * 0.6 + BULLET_HALF

# Cheat mode
CHEAT_SPIN_SPEED    = 100.0   # gun rotation in degrees per second
CHEAT_FIRE_INTERVAL = 0.20    # minimum seconds between automatic shots

# Camera
CAM_START_ANGLE  = 200.0
CAM_START_HEIGHT = 500.0
CAM_RADIUS       = 850.0
CAM_MIN_HEIGHT   = 80.0
CAM_MAX_HEIGHT   = 1500.0
CAM_HEIGHT_STEP  = 25.0
CAM_ANGLE_STEP   = 2.5
EYE_HEIGHT       = 88.0       # first-person camera height

# Timing
TARGET_FPS = 120              # caps the idle loop so it doesn't pin a CPU core
MAX_DT     = 0.05             # longest step simulated in one frame

# HUD
HUD_FONT   = GLUT_BITMAP_HELVETICA_18
TITLE_FONT = GLUT_BITMAP_TIMES_ROMAN_24
WHITE  = (1.0, 1.0, 1.0)
YELLOW = (1.0, 1.0, 0.0)
RED    = (1.0, 0.3, 0.3)


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

win_w, win_h = WINDOW_W, WINDOW_H

px, py  = 0.0, 0.0      # player position
gun_ang = 0.0           # gun heading in degrees; 0 points along +Y

cam_ang      = CAM_START_ANGLE
cam_h        = CAM_START_HEIGHT
first_person = False

bullets = []            # each bullet: [x, y, z, vx, vy]
enemies = []            # each enemy: {'x', 'y', 't'}, t = pulse phase

lives     = START_LIVES
score     = 0
missed    = 0
game_over = False
game_over_reason = ""

cheat        = False
cheat_vision = False
cheat_timer  = 0.0

last_time = time.perf_counter()
quadric   = None        # shared GLU quadric, created once the window exists


# ---------------------------------------------------------------------------
# Game logic
# ---------------------------------------------------------------------------

def _gun_dir():
    """Unit vector in the direction the gun currently points."""
    r = math.radians(gun_ang)
    return -math.sin(r), math.cos(r)


def _enemy_scale(e):
    """Current pulse scale of an enemy, oscillating between 0.6 and 1.0."""
    return 0.8 + 0.2 * math.sin(e['t'])


def _hit_radius(e):
    """Distance at which a bullet's centre touches this enemy."""
    return ENEMY_BODY_R * _enemy_scale(e) + BULLET_HALF


def _dist_to_segment(qx, qy, ax, ay, bx, by):
    """Shortest distance from point Q to the line segment A-B."""
    abx, aby = bx - ax, by - ay
    len2 = abx * abx + aby * aby
    t = 0.0
    if len2 > 0:
        t = max(0.0, min(1.0, ((qx - ax) * abx + (qy - ay) * aby) / len2))
    return math.hypot(qx - (ax + abx * t), qy - (ay + aby * t))


def fire():
    """Spawn a bullet at the gun muzzle travelling in the gun direction."""
    dx, dy = _gun_dir()
    bullets.append([px + dx * MUZZLE_OFFSET, py + dy * MUZZLE_OFFSET,
                    MUZZLE_HEIGHT, dx * BULLET_SPEED, dy * BULLET_SPEED])
    print("Player bullet fired!")


def _spawn_enemy():
    """Create an enemy at a random spot, preferably well away from the player."""
    lo, hi = -ARENA + 80, ARENA - 80
    for _ in range(300):
        x, y = random.uniform(lo, hi), random.uniform(lo, hi)
        if math.hypot(x - px, y - py) > SPAWN_MIN_GAP:
            break
    return {'x': x, 'y': y, 't': random.uniform(0, math.tau)}


def reset():
    """Start a fresh game."""
    global px, py, gun_ang, cam_ang, cam_h, first_person
    global lives, score, missed, game_over, game_over_reason
    global cheat, cheat_vision, cheat_timer

    px, py, gun_ang = 0.0, 0.0, 0.0
    cam_ang, cam_h, first_person = CAM_START_ANGLE, CAM_START_HEIGHT, False
    lives, score, missed = START_LIVES, 0, 0
    game_over, game_over_reason = False, ""
    cheat, cheat_vision, cheat_timer = False, False, 0.0

    bullets.clear()
    enemies[:] = [_spawn_enemy() for _ in range(ENEMY_COUNT)]
    print(f"New game! Lives: {lives} | Score: {score} | Missed: {missed}")


def _end_game(reason):
    global game_over, game_over_reason
    game_over, game_over_reason = True, reason
    print(f"GAME OVER! {reason}. Final score: {score}")


def _bullet_hit(x0, y0, x1, y1):
    """Index of the first enemy touched by a bullet moving from (x0, y0) to
    (x1, y1), or None. Testing the whole path keeps fast bullets from
    skipping through an enemy between frames."""
    for i, e in enumerate(enemies):
        if _dist_to_segment(e['x'], e['y'], x0, y0, x1, y1) < _hit_radius(e):
            return i
    return None


def _enemy_in_sights():
    """True if a bullet fired right now would hit an enemy."""
    gx, gy = _gun_dir()
    for e in enemies:
        ex, ey = e['x'] - px, e['y'] - py
        ahead = ex * gx + ey * gy           # distance along the line of fire
        off   = abs(ex * gy - ey * gx)      # distance away from the line of fire
        if ahead > 0 and off < MIN_HIT_RADIUS * 0.8:
            return True
    return False


def update(dt):
    """Advance the simulation by dt seconds."""
    global gun_ang, lives, score, missed, cheat_timer

    if game_over:
        return

    if cheat:
        gun_ang = (gun_ang + CHEAT_SPIN_SPEED * dt) % 360

    # Enemies pulse and creep toward the player.
    for e in enemies:
        e['t'] += PULSE_SPEED * dt
        dx, dy = px - e['x'], py - e['y']
        d = math.hypot(dx, dy)
        if d > 1:
            step = min(ENEMY_SPEED * dt, d)
            e['x'] += dx / d * step
            e['y'] += dy / d * step

    # Bullets either hit an enemy, leave the arena (a miss) or keep flying.
    flying = []
    for b in bullets:
        x0, y0 = b[0], b[1]
        b[0] += b[3] * dt
        b[1] += b[4] * dt
        hit = _bullet_hit(x0, y0, b[0], b[1])
        if hit is not None:
            score += 1
            enemies[hit] = _spawn_enemy()
            print(f"Enemy down! Score: {score}")
        elif abs(b[0]) > ARENA + 60 or abs(b[1]) > ARENA + 60:
            missed += 1
            print(f"Bullet missed! ({missed}/{MAX_MISSES})")
        else:
            flying.append(b)
    bullets[:] = flying

    if missed >= MAX_MISSES:
        _end_game("Too many missed shots")
        return

    # An enemy that reaches the player costs a life and respawns elsewhere.
    for i, e in enumerate(enemies):
        reach = ENEMY_BODY_R * _enemy_scale(e) + PLAYER_RADIUS
        if math.hypot(px - e['x'], py - e['y']) < reach:
            lives -= 1
            enemies[i] = _spawn_enemy()
            print(f"Hit by an enemy! Lives remaining: {lives}")
            if lives <= 0:
                _end_game("Out of lives")
                return

    # Cheat mode only pulls the trigger when the shot is lined up to hit,
    # so it never wastes bullets.
    if cheat:
        cheat_timer += dt
        if cheat_timer >= CHEAT_FIRE_INTERVAL and _enemy_in_sights():
            fire()
            cheat_timer = 0.0


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def draw_grid():
    """Checkerboard floor generated tile by tile."""
    n = ARENA // TILE
    glBegin(GL_QUADS)
    for i in range(-n, n):
        for j in range(-n, n):
            if (i + j) % 2 == 0:
                glColor3f(1.0, 1.0, 1.0)
            else:
                glColor3f(0.70, 0.50, 0.95)
            x0, y0 = i * TILE, j * TILE
            glVertex3f(x0,        y0,        0)
            glVertex3f(x0 + TILE, y0,        0)
            glVertex3f(x0 + TILE, y0 + TILE, 0)
            glVertex3f(x0,        y0 + TILE, 0)
    glEnd()


def draw_walls():
    """Four boundary walls, each a different colour."""
    G = ARENA
    wall_data = [
        ((0.0, 1.0, 0.0),   # south
         [(-G, -G, 0), ( G, -G, 0), ( G, -G, WALL_H), (-G, -G, WALL_H)]),
        ((0.0, 1.0, 1.0),   # north
         [(-G,  G, 0), ( G,  G, 0), ( G,  G, WALL_H), (-G,  G, WALL_H)]),
        ((0.0, 0.0, 1.0),   # west
         [(-G, -G, 0), (-G,  G, 0), (-G,  G, WALL_H), (-G, -G, WALL_H)]),
        ((0.0, 0.7, 0.0),   # east
         [( G, -G, 0), ( G,  G, 0), ( G,  G, WALL_H), ( G, -G, WALL_H)]),
    ]
    for color, verts in wall_data:
        glColor3f(*color)
        glBegin(GL_QUADS)
        for v in verts:
            glVertex3f(*v)
        glEnd()


def draw_player():
    """
    The player is built from simple primitives:
      - 2 cylinders  - legs
      - 1 cuboid     - torso
      - 1 sphere     - head
      - 1 cuboid     - gun barrel, with a small sphere at the muzzle
    The model faces +Y and is rotated to the gun heading. When the game is
    over the player tips over onto the floor.
    """
    glPushMatrix()
    glTranslatef(px, py, 0)
    if game_over:
        glRotatef(90, 0, 1, 0)
    glRotatef(gun_ang, 0, 0, 1)

    # Legs
    glColor3f(0.10, 0.10, 0.90)
    for side in (-12, 12):
        glPushMatrix()
        glTranslatef(side, 0, 0)
        gluCylinder(quadric, 8, 4, 42, 8, 3)
        glPopMatrix()

    # Torso
    glColor3f(0.10, 0.50, 0.10)
    glPushMatrix()
    glTranslatef(0, 0, 56)
    glScalef(1.5, 0.75, 1.0)
    glutSolidCube(38)
    glPopMatrix()

    # Head
    glColor3f(0.92, 0.76, 0.60)
    glPushMatrix()
    glTranslatef(0, 0, 90)
    gluSphere(quadric, 19, 12, 12)
    glPopMatrix()

    # Gun barrel
    glColor3f(0.40, 0.40, 0.40)
    glPushMatrix()
    glTranslatef(0, 26, 56)
    glScalef(0.22, 2.4, 0.22)
    glutSolidCube(30)
    glPopMatrix()

    # Muzzle
    glColor3f(0.20, 0.20, 0.20)
    glPushMatrix()
    glTranslatef(0, 40, 56)
    gluSphere(quadric, 5, 6, 6)
    glPopMatrix()

    glPopMatrix()


def draw_enemy(e):
    """Red body sphere topped with a black head, pulsing in size over time."""
    s = _enemy_scale(e)
    glPushMatrix()
    glTranslatef(e['x'], e['y'], ENEMY_BODY_R * s)

    glColor3f(0.90, 0.05, 0.05)
    gluSphere(quadric, ENEMY_BODY_R * s, 12, 12)

    glColor3f(0.05, 0.05, 0.05)
    glTranslatef(0, 0, (ENEMY_BODY_R + ENEMY_HEAD_R) * s)
    gluSphere(quadric, ENEMY_HEAD_R * s, 10, 10)

    glPopMatrix()


def draw_bullet(b):
    """Bullet = small yellow cube."""
    glPushMatrix()
    glTranslatef(b[0], b[1], b[2])
    glColor3f(1.0, 0.90, 0.0)
    glutSolidCube(BULLET_HALF * 2)
    glPopMatrix()


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------

def _begin_2d():
    """Switch to window-pixel coordinates (origin = bottom-left)."""
    glDisable(GL_DEPTH_TEST)
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(0, win_w, 0, win_h)
    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()


def _end_2d():
    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST)


def _text_width(text, font=HUD_FONT):
    return sum(glutBitmapWidth(font, ord(ch)) for ch in text)


def draw_text(x, y, text, color=WHITE, font=HUD_FONT):
    """Draw a string at window coordinates. Call between _begin_2d/_end_2d."""
    glColor3f(*color)
    glRasterPos2f(x, y)
    for ch in text:
        glutBitmapCharacter(font, ord(ch))


def draw_text_centered(y, text, color=WHITE, font=HUD_FONT):
    draw_text((win_w - _text_width(text, font)) / 2, y, text, color, font)


def _draw_panel(x0, y0, x1, y1, alpha=0.55):
    """Translucent dark box that keeps HUD text readable over the bright floor."""
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(0.0, 0.0, 0.0, alpha)
    glBegin(GL_QUADS)
    glVertex2f(x0, y0)
    glVertex2f(x1, y0)
    glVertex2f(x1, y1)
    glVertex2f(x0, y1)
    glEnd()
    glDisable(GL_BLEND)


def draw_hud():
    lines = [
        (f"Player Life Remaining: {lives}", WHITE),
        (f"Game Score: {score}", WHITE),
        (f"Player Bullet Missed: {missed} / {MAX_MISSES}", WHITE),
    ]
    if cheat:
        vision = "ON" if cheat_vision else "OFF"
        lines.append((f"CHEAT MODE: ON  |  Cheat vision (V): {vision}", YELLOW))
    if first_person:
        lines.append(("Camera: First-Person", WHITE))

    _begin_2d()

    line_h = 25
    top = win_h - 30
    width = max(_text_width(text) for text, _ in lines)
    _draw_panel(0, top - line_h * (len(lines) - 1) - 12, width + 24, win_h)
    for k, (text, color) in enumerate(lines):
        draw_text(12, top - k * line_h, text, color)

    if game_over:
        cy = win_h // 2
        _draw_panel(0, cy - 55, win_w, cy + 65)
        draw_text_centered(cy + 25, "*** GAME OVER ***", RED, TITLE_FONT)
        draw_text_centered(cy - 5, f"{game_over_reason}  |  Final score: {score}")
        draw_text_centered(cy - 35, "Press R to restart")

    _end_2d()


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def setup_camera():
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(60, win_w / win_h, 0.5, 4000)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    if first_person:
        # Eye sits just in front of the player's head, looking down the gun.
        dx, dy = _gun_dir()
        ex, ey = px + dx * 22, py + dy * 22

        if cheat and cheat_vision and enemies:
            nearest = min(enemies, key=lambda e: math.hypot(e['x'] - px, e['y'] - py))
            lx, ly, lz = nearest['x'], nearest['y'], ENEMY_BODY_R * 0.8
        else:
            lx, ly, lz = px + dx * 600, py + dy * 600, EYE_HEIGHT

        gluLookAt(ex, ey, EYE_HEIGHT, lx, ly, lz, 0, 0, 1)
    else:
        # Third-person camera orbiting the player.
        r = math.radians(cam_ang)
        ex = px + CAM_RADIUS * math.cos(r)
        ey = py + CAM_RADIUS * math.sin(r)
        gluLookAt(ex, ey, cam_h, px, py, 0, 0, 0, 1)


# ---------------------------------------------------------------------------
# GLUT callbacks
# ---------------------------------------------------------------------------

def keyboardListener(key, x, y):
    global px, py, gun_ang, cheat, cheat_vision

    key = key.lower()               # works with Caps Lock on too

    if key == b'r':
        reset()
        return

    if game_over:
        return

    if key in (b'w', b's'):
        dx, dy = _gun_dir()
        sign = 1 if key == b'w' else -1
        limit = ARENA - EDGE_MARGIN
        px = max(-limit, min(limit, px + sign * dx * MOVE_STEP))
        py = max(-limit, min(limit, py + sign * dy * MOVE_STEP))
    elif key == b'a':
        gun_ang = (gun_ang + TURN_STEP) % 360
    elif key == b'd':
        gun_ang = (gun_ang - TURN_STEP) % 360
    elif key == b'c':
        cheat = not cheat
        print(f"Cheat mode {'ON' if cheat else 'OFF'}")
    elif key == b'v':
        cheat_vision = not cheat_vision
        print(f"Cheat vision {'ON' if cheat_vision else 'OFF'}")

    glutPostRedisplay()


def specialKeyListener(key, x, y):
    global cam_ang, cam_h

    if key == GLUT_KEY_UP:
        cam_h = min(CAM_MAX_HEIGHT, cam_h + CAM_HEIGHT_STEP)
    elif key == GLUT_KEY_DOWN:
        cam_h = max(CAM_MIN_HEIGHT, cam_h - CAM_HEIGHT_STEP)
    elif key == GLUT_KEY_LEFT:
        cam_ang = (cam_ang + CAM_ANGLE_STEP) % 360
    elif key == GLUT_KEY_RIGHT:
        cam_ang = (cam_ang - CAM_ANGLE_STEP) % 360

    glutPostRedisplay()


def mouseListener(button, state, x, y):
    global first_person

    if game_over or state != GLUT_DOWN:
        return

    if button == GLUT_LEFT_BUTTON:
        fire()
    elif button == GLUT_RIGHT_BUTTON:
        first_person = not first_person

    glutPostRedisplay()


def reshape(w, h):
    """Keep the viewport and aspect ratio correct when the window is resized."""
    global win_w, win_h
    win_w, win_h = max(1, w), max(1, h)
    glViewport(0, 0, win_w, win_h)


def idle():
    """
    Main game loop, driven by the GLUT idle callback. Everything moves by
    elapsed wall-clock time, so the game plays at the same speed on any
    machine; the loop is capped at TARGET_FPS to avoid burning CPU.
    """
    global last_time

    now = time.perf_counter()
    elapsed = now - last_time
    if elapsed < 1.0 / TARGET_FPS:
        time.sleep(1.0 / TARGET_FPS - elapsed)
        return
    last_time = now

    update(min(elapsed, MAX_DT))
    glutPostRedisplay()


def showScreen():
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glViewport(0, 0, win_w, win_h)

    setup_camera()

    draw_grid()
    draw_walls()
    draw_player()
    for e in enemies:
        draw_enemy(e)
    for b in bullets:
        draw_bullet(b)

    draw_hud()

    glutSwapBuffers()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global quadric, last_time

    glutInit()
    glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGB | GLUT_DEPTH)
    glutInitWindowSize(WINDOW_W, WINDOW_H)
    glutInitWindowPosition(0, 0)
    glutCreateWindow(WINDOW_TITLE)

    glEnable(GL_DEPTH_TEST)
    quadric = gluNewQuadric()

    random.seed()
    reset()

    glutDisplayFunc(showScreen)
    glutReshapeFunc(reshape)
    glutKeyboardFunc(keyboardListener)
    glutSpecialFunc(specialKeyListener)
    glutMouseFunc(mouseListener)
    glutIdleFunc(idle)

    last_time = time.perf_counter()
    glutMainLoop()


if __name__ == "__main__":
    main()
