#!/usr/bin/env python3
"""Hello - the Appmart demo app. A tiny hello from Bolt."""
import math
import os
import sys
import time

import pygame

sys.path.insert(0, "/opt/gpi/common")
from gpi_ui import W, H, BLACK, WHITE, DIM  # noqa: E402
from bolt import Bolt  # noqa: E402


def main():
    pygame.init()
    pygame.mouse.set_visible(False)
    screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
    clock = pygame.time.Clock()
    f_big = pygame.font.SysFont("dejavusans", 52, bold=True)
    f_small = pygame.font.SysFont("dejavusans", 16)
    bolt = Bolt(scale=1.4)

    running = True
    while running:
        t = time.time()
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE,):
                running = False

        for y in range(0, H, 4):
            k = y / H
            c = (int(10 + 14 * k), int(14 + 10 * k), int(30 + 18 * k))
            pygame.draw.line(screen, c, (0, y), (W, y))
        bob = int(8 * math.sin(t * 2))
        bolt.draw(screen, W // 2 - 70, 150 + bob, "happy", t)
        txt = f_big.render("Hello!", True, WHITE)
        screen.blit(txt, txt.get_rect(center=(W // 2, 400)))
        sub = f_small.render("You slotted me in from Appmart.", True, DIM)
        screen.blit(sub, sub.get_rect(center=(W // 2, 445)))
        pygame.display.flip()
        clock.tick(30)


if __name__ == "__main__":
    main()
