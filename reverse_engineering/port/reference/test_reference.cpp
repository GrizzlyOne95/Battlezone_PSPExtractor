// Behaviour checks for the reference implementation. Build:
//   g++ -std=c++17 -O2 -o test_reference test_reference.cpp bzpsp_tank.cpp bzpsp_combat.cpp bzpsp_match.cpp
//       bzpsp_collision.cpp
// Each check prints the measured value next to the value derived from the PSP code/data.
#include <cmath>
#include <cstdio>
#include <cstdlib>

#include "bzpsp_ref.h"

using namespace bzpsp;

static int g_failures = 0;
static void check(const char* what, float got, float want, float tol) {
    bool ok = std::fabs(got - want) <= tol;
    std::printf("%-46s got %9.3f  want %9.3f  %s\n", what, got, want, ok ? "ok" : "FAIL");
    if (!ok) ++g_failures;
}

// Flat ground at y = 0.
static float flatGround(const Vec3& from, const Vec3& to) {
    if (from.y < 0 || to.y > 0) return -1;
    return from.y / (from.y - to.y);
}

static TankState makeTank(TankSize size) {
    TankState t;
    t.size = size;
    t.frame.pos = {0, 6, 0};
    spawnTank(t, kShippedTankMotion[size], 0);
    return t;
}

static void run(TankState& t, const TankInput& in, float seconds, float dt = 1.0f / 30.0f) {
    const TankMotion& m = kShippedTankMotion[t.size];
    for (float s = 0; s < seconds; s += dt) {
        driveStep(t, m, in, dt, flatGround);
        integrateBody(t, dt);
    }
}

int main() {
    std::puts("== hover tank (30 Hz, flat ground, minimal integrator) ==");
    {
        TankState t = makeTank(kMedium);
        run(t, {}, 8.0f);
        std::printf("%-46s %9.3f m\n", "medium idle ride height", t.frame.pos.y);
        check("idle stays upright (up.y)", t.frame.up.y, 1.0f, 0.02f);
        check("idle horizontal speed", std::hypot(t.linVel.x, t.linVel.z), 0.0f, 0.05f);
    }
    for (int s = 0; s < 3; ++s) {
        TankState t = makeTank(TankSize(s));
        TankInput in; in.throttle = 1;
        run(t, in, 12.0f);
        float fwd = t.frame.worldToLocal(t.linVel).z;
        char label[64]; std::snprintf(label, sizeof label, "size %d top speed (m/s)", s);
        check(label, fwd, kShippedTankMotion[s].maxFwdSpeed, 1.5f);
    }
    {
        TankState t = makeTank(kSmall);
        TankInput in; in.throttle = -1;
        run(t, in, 12.0f);
        check("small reverse speed = 75% of max", -t.frame.worldToLocal(t.linVel).z,
              kShippedTankMotion[kSmall].maxFwdSpeed * 0.75f, 1.5f);
    }
    {
        TankState t = makeTank(kSmall);
        TankInput in; in.steer = 1;
        run(t, in, 4.0f);
        check("small yaw rate at full lock (rad/s)", std::fabs(t.angVel.y),
              kShippedTankMotion[kSmall].turnRate / k::kAngularDecay, 0.15f);
    }
    {
        TankState t = makeTank(kMedium);
        t.nitroEngaged = true; t.nitroMult = 1.5f; t.nitroDuration = 3.0f;
        TankInput in; in.throttle = 1; in.boost = true;
        run(t, in, 2.0f);
        check("nitro meter after 2 s of a 3 s boost", t.nitroMeter, 1.0f - 2.0f / 3.0f, 0.02f);
    }

    std::puts("== damage ==");
    {
        TankState t = makeTank(kMedium); DamageState d;
        d.shieldActive = true; d.shieldHp = 150;
        takeDamage(t, d, 170, 3, 0);
        check("Armor pickup absorbs 150 of a 170 hit", t.hp, 200 - 20, 0.001f);
        TankState f = makeTank(kLarge); DamageState fd;
        takeDamage(f, fd, 25, 8, 0.0f); takeDamage(f, fd, 25, 8, 0.5f);
        check("Fusion 3rd hit within 2 s deals 25 x 15", takeDamage(f, fd, 25, 8, 1.0f), 375, 0.001f);
        TankState g = makeTank(kLarge); DamageState gd;
        takeDamage(g, gd, 25, 8, 0.0f); takeDamage(g, gd, 25, 8, 3.0f);
        check("Fusion hits 3 s apart: no combo", takeDamage(g, gd, 25, 8, 3.5f), 25, 0.001f);
        check("splash at half radius (400 dmg, r 35)", splashDamageAt(400, 35, 17.5f), 200, 0.001f);
    }

    std::puts("== match rules ==");
    {
        Match mt; mt.mode = kDM; mt.state = kPlaying; mt.timeLimit = mt.originalTimeLimit = 600;
        mt.players.resize(2);
        mt.players[0].score = 3; mt.players[1].score = 3;
        mt.update(600.0f, false);
        check("tie at time-out adds max(10%, 60 s)", mt.timeLimit, 660, 0.001f);
        mt.onKill(1, 0, false);
        mt.update(60.0f, false);
        check("overtime decided -> winner", float(mt.winner), 0, 0);
    }
    {
        Match mt; mt.mode = kCTF; mt.state = kPlaying; mt.players.resize(2);
        mt.players[1].team = 1;
        mt.onKill(1, 0, true);
        check("CTF: killing the flag carrier", float(mt.players[0].score), 5, 0);
    }
    check("HZ: 2 pads -> seconds per point", kHzPointInterval[2], 2.0f, 0);
    {
        KoCore core;
        core.takeDamage(1600);
        check("KO: shield soaks 1600 first", core.health, 100, 0);
        bool killed = core.takeDamage(100);
        check("KO: next 100 destroys the core", float(killed), 1, 0);
        core.heal(10 * kKoPadRatio);
        check("KO: one pad tick revives with 20 hp", core.health, 20, 0);
    }

    // Collisions (PORT_SPEC.md §4.6)
    {
        const PhysMaterial world{0.55f, 0.84f, 1.0f}, tank{0.15f, 0.3f, 0.0f};  // ids 0 and 11
        ContactCoeffs tw = combineMaterials(tank, world), tt = combineMaterials(tank, tank);
        check("tank-world friction mu", tw.mu, 0.252f, 1e-6f);
        check("tank-world restitution e", tw.e, 0.0825f, 1e-6f);
        check("tank-world grip c", tw.c, 0.5f, 0);
        check("tank-tank grip c (frictionless)", tt.c, 0.0f, 0);
        Separation s = separate(3.0f, {0, 1, 0}, 100, 100, true, false, true, false);
        check("push-out clamped to 1 m (tank vs world)", s.moveA.y, 1.0f, 1e-6f);
        s = separate(0.5f, {1, 0, 0}, 100, 2, true, true, true, false);
        check("tank shoves an awake object, not itself", s.moveB.x, -0.5f, 1e-6f);
        check("... and doesn't move", s.moveA.x, 0.0f, 0);
        check("AI tank breaks a breakable at rest", breakableRamDamage(0, 70, false, 100), 1000, 0);
        check("human needs > half speed", breakableRamDamage(30, 70, true, 100), 0, 0);
        check("ram scale caps at 1.5", ramScale(200, 70), 1.5f, 0);
        ExplosiveContact early = explosiveContact(11, 0.5f, false, true);
        check("mine not armed at 0.5 s: no detonation", float(early.detonate), 0, 0);
        ExplosiveContact mortar = explosiveContact(18, 0.0f, true, false);
        check("mortar explodes on the ground at once", float(mortar.detonate), 1, 0);
    }

    std::printf("\n%d failure(s)\n", g_failures);
    return g_failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
