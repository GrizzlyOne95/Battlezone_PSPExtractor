// Damage, splash and projectile motion (Vehicle 0x99be8/0x98808, ProjectileMgr 0x7328/0xb998/0x64e8).
#include <algorithm>
#include <cmath>

#include "bzpsp_ref.h"

namespace bzpsp {

float takeDamage(TankState& t, DamageState& d, float damage, int bulletId, float now) {
    if (d.invulnerable || t.dead) return 0;

    // 3-hit combo (0x98808): Swarm (bullet 14) x13, A.E. Fusion (bullet 8) x15 on the 3rd hit,
    // provided each gap between consecutive hits is <= 2 s; otherwise the oldest hit is dropped.
    static const int kComboBullet[2] = {14, 8};
    static const float kComboMult[2] = {13.0f, 15.0f};
    for (int c = 0; c < 2; ++c) {
        if (bulletId != kComboBullet[c]) continue;
        auto& times = d.comboTimes[c];
        times[d.comboCount[c]] = now;
        if (++d.comboCount[c] == 3) {
            bool tooSlow = false;
            for (int i = 0; i < 2; ++i) {
                if (times[i + 1] - times[i] > 2.0f) {  // drop the oldest hit, keep counting
                    times[0] = times[1]; times[1] = times[2]; times[2] = 0;
                    d.comboCount[c] = 2;
                    tooSlow = true;
                    break;
                }
            }
            if (!tooSlow) {
                damage *= kComboMult[c];
                times = {0, 0, 0};
                d.comboCount[c] = 0;
            }
        }
    }

    if (d.armorActive) damage *= d.armorScale;
    if (d.shieldActive) {
        if (d.shieldHp < damage) { damage -= d.shieldHp; d.shieldActive = false; d.shieldHp = 0; }
        else { d.shieldHp -= damage; damage = 0; }
    }
    if (damage > 0) t.sinceDamage = 0;  // also breaks invisibility
    t.hp -= damage;
    if (t.hp <= 0) { t.hp = 0; t.dead = true; }
    return damage;
}

float splashDamageAt(float damage, float radius, float dist) {
    if (radius <= 0 || dist >= radius) return 0;
    return damage * (1.0f - dist / radius);
}

Projectile spawnProjectile(const ProjectileDef& def, const Frame& muzzle, float shooterSpeed,
                           float weaponDamageBonus, float weaponStealBonus) {
    Projectile p;
    p.def = &def;
    p.frame = muzzle;
    float speed = def.velocity;
    if (shooterSpeed * 0.05f > 0) speed += shooterSpeed * 0.05f;
    p.velPerUpdate = muzzle.at * speed;
    p.damage = def.damage + weaponDamageBonus;   // Damage tweak is a flat bonus (weapon +0x248)
    p.steal = def.stealScale + weaponStealBonus; // Vampire tweak (weapon +0x24c)
    p.lockTimer = 0;
    return p;
}

void steerMissile(Projectile& p, const Vec3& targetPos, const Vec3& targetVel, float dt) {
    if (p.lockTimer < p.def->lockDelay) { p.lockTimer += dt; return; }
    Vec3 dir = normalize(p.velPerUpdate);
    Vec3 aim = normalize(targetPos + targetVel * dt - p.frame.pos);
    Vec3 axis = cross(dir, aim);
    float sinA = std::min(1.0f, length(axis));
    float angleDeg = std::asin(sinA) * 57.29578f;
    float stepDeg = std::min(p.def->maxTurnRate * dt, angleDeg);
    if (stepDeg <= 0) return;
    p.frame.rotate(normalize(axis) * (stepDeg / 57.29578f));
    float speed = length(p.velPerUpdate);
    p.velPerUpdate = p.frame.at * speed;
}

bool advanceProjectile(Projectile& p, float dt) {
    p.age += dt;
    if (p.age >= p.def->lifeSpan) return false;  // expire (explosives detonate here)
    p.frame.pos += p.velPerUpdate;               // per update, not per second
    return true;
}

}  // namespace bzpsp
