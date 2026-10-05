#!/usr/bin/env python3
"""Generate icon.png for Appmart and its demo app. Run on the GPi."""
import os
import pygame

os.environ["SDL_VIDEODRIVER"] = "dummy"
pygame.init()
pygame.font.init()

SZ = 128


def save(surf, path):
    pygame.image.save(surf, path)
    print("wrote", path)


def appmart_icon(path):
    s = pygame.Surface((SZ, SZ), pygame.SRCALPHA)
    # warm dark rounded tile
    pygame.draw.rect(s, (36, 26, 17), (0, 0, SZ, SZ), border_radius=26)
    pygame.draw.rect(s, (255, 176, 32), (0, 0, SZ, SZ), 5, border_radius=26)
    # awning stripes
    for i in range(6):
        x = 14 + i * 17
        col = (255, 176, 32) if i % 2 == 0 else (245, 230, 205)
        pygame.draw.rect(s, col, (x, 18, 17, 22), border_radius=4)
    pygame.draw.rect(s, (74, 50, 30), (14, 38, 100, 8), border_radius=4)
    # shopping bag
    bag = [(44, 58), (84, 58), (78, 110), (50, 110)]
    pygame.draw.polygon(s, (255, 176, 32), bag)
    pygame.draw.arc(s, (36, 26, 17), (52, 44, 24, 26), 0, 3.14, 5)
    f = pygame.font.SysFont("dejavusans", 20, bold=True)
    t = f.render("shop", True, (245, 230, 205))
    s.blit(t, t.get_rect(center=(SZ // 2, 116)))
    save(s, path)


def hello_icon(path):
    s = pygame.Surface((SZ, SZ), pygame.SRCALPHA)
    pygame.draw.rect(s, (58, 134, 255), (0, 0, SZ, SZ), border_radius=26)
    pygame.draw.rect(s, (30, 80, 180), (0, 0, SZ, SZ), 5, border_radius=26)
    f = pygame.font.SysFont("dejavusans", 64, bold=True)
    t = f.render("Hi", True, (255, 255, 255))
    s.blit(t, t.get_rect(center=(SZ // 2, SZ // 2 - 6)))
    save(s, path)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    appmart_icon(os.path.join(here, "icon.png"))
    hello_icon(os.path.join(here, "demo", "hello", "icon.png"))
