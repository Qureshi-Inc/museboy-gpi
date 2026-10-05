"""Bolt the builder bot - shared mascot for GPi console apps.

Moods: idle, listen, think, build, happy, sad.
Usage:
    from bolt import Bolt
    bolt = Bolt()
    bolt.draw(surface, x, y, mood="idle", scale=1.0)
"""
import math
import time

import pygame

AMBER = (255, 176, 32)
CYAN = (110, 200, 250)
RED = (255, 100, 100)
WHITE = (235, 235, 240)
DARK = (24, 24, 32)

class Bolt:
    """Bolt the builder bot. Moods: idle, listen, think, build, happy, sad."""

    def __init__(self):
        self.t0 = time.time()

    def draw(self, s, x, y, mood="idle", scale=1.0):
        t = time.time() - self.t0
        bob = math.sin(t * 2.2) * 4 * scale
        if mood == "happy":
            bob = -abs(math.sin(t * 5)) * 14 * scale
        yy = y + bob

        # shadow
        pygame.draw.ellipse(s, (0, 0, 0, 90),
                            (x - 52 * scale, y + 78 * scale,
                             104 * scale, 16 * scale))

        body_c = (86, 128, 190)
        head_c = (168, 212, 245)
        dark = (30, 45, 75)

        # legs + boots
        lw = 20 * scale
        for dx in (-1, 1):
            lx = x + dx * 22 * scale
            pygame.draw.rect(s, dark,
                             (lx - lw / 2, yy + 46 * scale, lw, 30 * scale),
                             border_radius=6)
            pygame.draw.rect(s, (50, 60, 85),
                             (lx - lw / 2 - 3, yy + 68 * scale, lw + 6,
                              12 * scale), border_radius=5)

        # body
        bw, bh = 76 * scale, 62 * scale
        pygame.draw.rect(s, body_c,
                         (x - bw / 2, yy - 8 * scale, bw, bh), border_radius=16)
        # chest panel with rivets
        pygame.draw.rect(s, (60, 95, 150),
                         (x - 24 * scale, yy + 6 * scale, 48 * scale,
                          30 * scale), border_radius=8)
        for i, ry in enumerate((14, 28)):
            for rx in (-14, 0, 14):
                pygame.draw.circle(s, AMBER,
                                   (int(x + rx * scale), int(yy + ry * scale)),
                                   int(3.5 * scale))

        # left arm (down)
        pygame.draw.line(s, body_c, (x - bw / 2, yy + 12 * scale),
                         (x - bw / 2 - 20 * scale, yy + 34 * scale),
                         int(13 * scale))
        pygame.draw.circle(s, head_c,
                           (int(x - bw / 2 - 20 * scale),
                            int(yy + 34 * scale)), int(8 * scale))

        # right arm depends on mood
        sx, sy = x + bw / 2, yy + 12 * scale
        if mood == "happy":
            ex, ey = sx + 26 * scale, sy - 34 * scale
        elif mood == "think":
            ex, ey = sx - 6 * scale, sy - 52 * scale  # hand to chin
        elif mood == "build":
            ham = math.sin(t * 9) * 16 * scale
            ex, ey = sx + 22 * scale, sy - 40 * scale + ham
        elif mood == "sad":
            ex, ey = sx + 14 * scale, sy + 30 * scale
        else:  # idle wave / listen / present
            wave = math.sin(t * 3.2) * 8 * scale
            ex, ey = sx + 26 * scale, sy - 18 * scale + wave
        pygame.draw.line(s, body_c, (sx, sy), (ex, ey), int(13 * scale))
        pygame.draw.circle(s, head_c, (int(ex), int(ey)), int(8 * scale))
        if mood == "build":
            # hammer
            hx, hy = ex, ey - 14 * scale
            pygame.draw.line(s, (120, 85, 50), (ex, ey), (hx, hy),
                             int(7 * scale))
            pygame.draw.rect(s, (90, 95, 110),
                             (hx - 16 * scale, hy - 22 * scale, 32 * scale,
                              16 * scale), border_radius=4)

        # head
        hr = 44 * scale
        hx, hy = x, yy - 52 * scale
        pygame.draw.circle(s, head_c, (int(hx), int(hy)), int(hr))

        # construction helmet
        pygame.draw.arc(s, AMBER,
                        (hx - hr, hy - hr - 14 * scale, hr * 2, hr * 2),
                        math.pi * 0.05, math.pi * 0.95, int(26 * scale))
        pygame.draw.rect(s, AMBER,
                         (hx - hr - 8 * scale, hy - 16 * scale,
                          hr * 2 + 16 * scale, 10 * scale), border_radius=5)
        # helmet lamp
        lamp_on = (int(t * 2) % 2) == 0
        pygame.draw.circle(s, (255, 230, 150) if lamp_on else (120, 100, 60),
                           (int(hx), int(hy - hr - 12 * scale)),
                           int(7 * scale))

        # face
        blink = (t % 3.4) < 0.12
        ey = hy - 8 * scale
        ex_off = 17 * scale
        if mood == "sad":
            # droopy eyes
            for sgn in (-1, 1):
                pygame.draw.circle(s, WHITE,
                                   (int(hx + sgn * ex_off), int(ey + 4 * scale)),
                                   int(11 * scale))
                pygame.draw.circle(s, dark,
                                   (int(hx + sgn * ex_off), int(ey + 8 * scale)),
                                   int(5 * scale))
                pygame.draw.line(s, dark,
                                 (hx + sgn * ex_off - 11 * scale, ey - 6 * scale),
                                 (hx + sgn * ex_off + 11 * scale, ey - 2 * scale),
                                 3)
            # frown
            pygame.draw.arc(s, dark,
                            (hx - 16 * scale, hy + 8 * scale, 32 * scale,
                             20 * scale), math.pi * 1.15, math.pi * 1.85, 3)
        elif blink and mood != "listen":
            for sgn in (-1, 1):
                pygame.draw.line(s, dark,
                                 (hx + sgn * ex_off - 10 * scale, ey),
                                 (hx + sgn * ex_off + 10 * scale, ey), 3)
            pygame.draw.arc(s, dark,
                            (hx - 15 * scale, hy + 2 * scale, 30 * scale,
                             18 * scale), math.pi * 1.1, math.pi * 1.9, 3)
        else:
            wide = 1.25 if mood == "listen" else 1.0
            for sgn in (-1, 1):
                pygame.draw.circle(s, WHITE,
                                   (int(hx + sgn * ex_off), int(ey)),
                                   int(12 * scale * wide))
                look = 3 * scale if mood == "think" else 0
                pygame.draw.circle(s, dark,
                                   (int(hx + sgn * ex_off),
                                    int(ey - look)), int(5.5 * scale))
                pygame.draw.circle(s, WHITE,
                                   (int(hx + sgn * ex_off + 2 * scale),
                                    int(ey - 2 * scale - look)),
                                   int(2 * scale))
            if mood == "think":
                # thinking smile, eyes glance up
                pygame.draw.arc(s, dark,
                                (hx - 14 * scale, hy + 4 * scale, 28 * scale,
                                 16 * scale), math.pi * 1.15, math.pi * 1.85, 3)
            else:
                pygame.draw.arc(s, dark,
                                (hx - 16 * scale, hy, 32 * scale, 22 * scale),
                                math.pi * 0.15, math.pi * 0.85, 3)

        # cheeks
        for sgn in (-1, 1):
            pygame.draw.circle(s, (255, 150, 150),
                               (int(hx + sgn * 30 * scale), int(hy + 12 * scale)),
                               int(6 * scale))

        # antenna
        pygame.draw.line(s, dark, (hx + hr - 8 * scale, hy - hr + 14 * scale),
                         (hx + hr + 6 * scale, hy - hr - 12 * scale), 3)
        pygame.draw.circle(s, RED if (int(t * 3) % 2) else (120, 40, 40),
                           (int(hx + hr + 6 * scale), int(hy - hr - 12 * scale)),
                           int(5 * scale))

        if mood == "listen":
            # sound waves
            for i in range(3):
                r = 20 + ((t * 60 + i * 22) % 66)
                alpha = max(0, 200 - int(r * 2.4))
                c = (CYAN[0], CYAN[1], CYAN[2])
                pygame.draw.arc(s, c,
                                (hx + hr - 6 * scale - r, hy - r,
                                 r * 2, r * 2),
                                -0.7, 0.7, 2)
        if mood == "build":
            # motion ticks near hammer
            for i in range(3):
                off = (t * 120 + i * 14) % 42
                pygame.draw.line(s, AMBER, (ex + 20 * scale, ey - 30 * scale - off),
                                 (ex + 30 * scale, ey - 30 * scale - off), 3)


