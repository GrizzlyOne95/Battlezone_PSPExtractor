// Collision response and contact rules (physics library 0x11b658 / 0x13661c / 0x145b2c, game
// callbacks 0x3ee94 / 0x3f01c). See PORT_SPEC.md §4.6.
#include <algorithm>
#include <cmath>

#include "bzpsp_ref.h"

namespace bzpsp {

const CollisionSphere kTankSpheres[kTankSphereCount] = {
    // .data 0x24e064 (+ y = 0.5 patched in for the four corners on first use), radius 2.5
    {{3.0f, 0.5f, 5.0f}, 2.5f},
    {{-3.0f, 0.5f, 5.0f}, 2.5f},
    {{3.0f, 0.5f, -5.0f}, 2.5f},
    {{-3.0f, 0.5f, -5.0f}, 2.5f},
    {{0.0f, 1.2f, 0.0f}, 2.5f},
};

ContactCoeffs combineMaterials(const PhysMaterial& a, const PhysMaterial& b) {
    return {a.friction * b.friction, a.restitution * b.restitution, (a.grip + b.grip) * 0.5f};
}

Vec3 contactImpulse(const ContactCoeffs& k, const Vec3& vRel, const Mat3& K, const Mat3& Kinv) {
    // Normal impulse that just stops the approach, and the impulse that stops the contact
    // point completely (sticking).
    const float jn = -vRel.z / K.r[2].z;
    const Vec3 stick = Kinv.mulRow(vRel) * -1.0f;
    const Vec3 d{stick.x, stick.y, stick.z - jn};  // tangential part of sticking
    const float pn = (k.e + 1.0f) * jn;             // restitution on the normal part
    Vec3 p{d.x * k.c, d.y * k.c, pn + d.z * k.c};   // grip scales the sticking part
    // Coulomb cone: if |p_t| > mu * p_z, slide along d until the cone is met.
    if (k.mu * p.z * k.mu * p.z < p.x * p.x + p.y * p.y + 0.0f) {
        const float s = std::sqrt(d.x * d.x + d.y * d.y + 0.0f) - k.mu * d.z;
        if (std::fabs(s) > 1e-5f) {
            const float t = (k.mu * (k.e + 1.0f) * jn) / s;
            p = {d.x * t, d.y * t, pn + d.z * t};
        } else {
            p = {0, 0, 0};
        }
    }
    return p;
}

Separation separate(float depth, const Vec3& normal, float massA, float massB, bool awakeA,
                    bool awakeB, bool tankA, bool tankB) {
    Vec3 push = normal * depth;
    const float len = length(push);
    if (len > 1.0f) push = push * (1.0f / len);
    if (awakeA && awakeB) {  // two awake bodies: a tank is never pushed (A checked first)
        if (tankA) awakeA = false;
        else if (tankB) awakeB = false;
    }
    const float mA = awakeA ? massA : 1e16f, mB = awakeB ? massB : 1e16f;
    const float sum = mA + mB;
    float wA = 0, wB = 0;
    if (double(sum) < 2e16) { wA = mB / sum; wB = mA / sum; }
    Separation out;
    if (wA > 1e-5f) out.moveA = push * wA;
    if (wB > 1e-5f) out.moveB = push * -wB;
    return out;
}

float breakableRamDamage(float fwdSpeed, float maxFwdSpeed, bool human, float breakableHp) {
    const float ratio = std::fabs(fwdSpeed) / maxFwdSpeed;
    if (!(ratio > 0.5f || breakableHp < 5.0f || !human)) return 0;
    float dmg = std::min(ratio, 1.5f) * 100.0f;
    if (!human) dmg += 1000.0f;
    return dmg;
}

float ramScale(float fwdSpeed, float maxFwdSpeed) {
    return std::min(fwdSpeed * 0.75f / maxFwdSpeed, 1.5f);
}

bool ramInFront(const Frame& rammer, const Vec3& targetPos) {
    const Vec3 dir = normalize(targetPos - rammer.pos);
    const float side = dot(dir, rammer.right);
    return side < 0.9f && side > -0.9f && dot(dir, rammer.at) >= 0.0f;
}

float explosiveArmTime(int projectileId) {
    switch (projectileId) {
        case 10: return 0.5f;
        case 11: return 1.0f;
        case 18: return 0.15f;
        default: return 0.0f;
    }
}

ExplosiveContact explosiveContact(int projectileId, float age, bool otherIsWorld, bool otherIsTank) {
    const bool mortar = projectileId == 18;
    if (otherIsWorld) return {mortar, mortar ? kContactIgnore : kContactRespond};
    if (age < explosiveArmTime(projectileId)) return {false, kContactIgnore};
    return {otherIsTank || mortar, kContactIgnore};
}

}  // namespace bzpsp
