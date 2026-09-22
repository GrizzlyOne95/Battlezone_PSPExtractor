// Hover tank movement, transcribed from HoverTank::0x94da0 (hover) and 0x954a0 (drive).
#include <algorithm>
#include <cfloat>

#include "bzpsp_ref.h"

namespace bzpsp {

const TankMotion kShippedTankMotion[3] = {
    // turn, fwdSpd, fwdAcc, sideSpd, sideAcc, hp, energy, eRech, suspH, damp, suspG, upright,
    // eStart, killBonus, regenDelay, regenAmt, splashR, splashDmg, shakeDur, shakeMag
    {21, 80, 100, 55, 95, 105, 300, 0, 10, 0.5f, 1000, 100, 1, 50, 6, 50, 60, 25, 1, 0.6f},
    {19, 68, 90, 55, 90, 200, 300, 0, 10, 0.5f, 1000, 100, 1, 100, 10, 75, 70, 35, 1, 0.8f},
    {17, 55, 70, 50, 70, 325, 300, 0, 10, 0.5f, 1000, 100, 1, 150, 15, 100, 80, 50, 1, 1.0f},
};

const EnhanceValue kShippedEnhance[kEnhanceCount] = {
    {5, 2}, {0.25f, 0.1f}, {5, 2}, {10, 5}, {2, 1}, {0.2f, 0.1f}, {50, 25}, {10, 5}, {15, 5}, {10, 5},
};

void Frame::rotate(const Vec3& axisAngle) {
    float angle = length(axisAngle);
    if (angle <= 0) return;
    Vec3 a = axisAngle * (1.0f / angle);
    float c = std::cos(angle), s = std::sin(angle);
    auto rot = [&](const Vec3& v) {  // Rodrigues
        return v * c + cross(a, v) * s + a * (dot(a, v) * (1 - c));
    };
    right = rot(right);
    up = rot(up);
    at = rot(at);
    orthonormalize();
}

void Frame::orthonormalize() {
    at = normalize(at);
    right = normalize(cross(up, at));
    up = cross(at, right);
}

void spawnTank(TankState& t, const TankMotion& m, float topSpeedBonus) {
    t.hpMax = t.hp = m.hitPoints;
    t.energyMax = m.energyPoints;
    t.energy = m.energyPoints * m.energyStart;
    t.maxFwdSpeed = m.maxFwdSpeed + topSpeedBonus;
    t.nitroMeter = 1;
    t.dead = false;
    t.sinceDamage = 0;
}

Impulse hoverStep(TankState& t, const TankMotion& m, float dt, const RaycastFn& raycast) {
    // NB: the PSP caches the ride height of the first tank that runs this code in a static
    // (0x2aea58). All shipped sizes use 10 m, so this reproduces it exactly.
    const float h = m.suspensionHeight;
    const Frame& f = t.frame;

    // One probe per tick, round robin.
    const int i = t.probeIndex;
    Vec3 p = f.transformPoint(k::kHoverProbes[i]);
    float hit = raycast(p, p - kWorldUp * h);
    float tHit = hit >= 0 ? hit : FLT_MAX;
    t.probeT[i] = tHit;
    t.probeIndex = (i + 1) & 3;

    Vec3 vLocal = f.worldToLocal(t.linVel);
    float spring = 0, damp = 0;
    if (tHit <= 1.0f) {
        float comp = (1.0f - tHit) * (h + 1.0f);
        spring = comp * comp;
        damp = -(m.hoverDampCoeff * h * 60.0f * dt * vLocal.y);
    }
    Impulse out;
    out.linear = f.localToWorld({0, damp, 0});
    out.linear.y += spring * m.suspensionGain * 0.5f * dt * f.up.y;

    // Torque from all four probes' latest compression.
    Vec3 tau;
    for (int k = 0; k < 4; ++k) {
        float tk = t.probeT[k];
        if (k > 0 && tk == 0.0f) { tk = t.probeT[0]; t.probeT[k] = tk; }
        float comp = std::max(0.0f, 1.0f - tk) * (h + 1.0f);
        float fk = comp * comp * m.suspensionGain * 0.5f * dt;
        Vec3 pk = f.transformPoint(k::kHoverProbes[k]);
        tau.z += fk * (pk.x - f.pos.x);
        tau.x += -(fk * (pk.z - f.pos.z));
    }

    // Self-righting: rotate up toward world up, strongly when tilted (2 - cos)^5.
    float c = dot(f.up, kWorldUp);
    Vec3 axis = cross(kWorldUp, f.up);
    if (c <= 0) axis = normalize(axis);
    float kUp = dt * m.uprightGain * 100.0f * std::pow(2.0f - c, 5.0f);
    tau -= axis * kUp;
    if (length(out.linear) == 0.0f) {  // no ground contact from this probe: double righting
        tau -= axis * kUp;
        t.airborne = true;
    } else {
        t.airborne = false;
    }
    tau = tau * k::kHoverTorqueScale;
    if (out.linear.y > 0 && t.tooSteep) out.linear.y = 0;
    out.angular = tau;
    return out;
}

void driveStep(TankState& t, const TankMotion& m, const TankInput& in, float dt, const RaycastFn& raycast) {
    dt = std::min(dt, k::kMaxDt);
    Frame& f = t.frame;

    // Too steep: nose up > 25 deg or roll > 25 deg.
    float pitch = dot(f.at, kWorldUp), roll = dot(f.right, kWorldUp);
    t.tooSteep = (k::kSteepSin < pitch) || (k::kSteepSin < std::fabs(roll));

    Impulse hov = hoverStep(t, m, dt, raycast);
    t.linVel += hov.linear * (1.0f / k::kMass);
    t.angVel += hov.angular * (1.0f / k::kInertia);

    float maxSpeed = t.maxFwdSpeed, turnRate = m.turnRate, strafe = in.strafe, throttle = in.throttle;
    if (t.slowed) { maxSpeed *= t.slowFactor; turnRate *= t.slowFactor; strafe *= t.slowFactor; }
    const float maxSide = m.maxSideSpeed;

    // Steering ramp: holding the stick at the stop ramps from 30% to 100% turn over 0.5 s.
    float steer = in.steer, hold;
    if (steer > 0.98f) { hold = (t.steerDir == 0) ? t.steerHold + dt : 0; t.steerDir = 0; }
    else if (steer < -0.98f) { hold = (t.steerDir == 1) ? t.steerHold + dt : 0; t.steerDir = 1; }
    else hold = 0;
    if (hold < 0) hold = 0;
    t.steerHold = hold;
    float ramp = std::clamp((hold * 2) * (hold * 2), 0.1f, 1.0f);
    double turnD = double(steer) * 0.3 + double(steer) * 0.7 * double(ramp);
    float turn = std::clamp(float(turnD), -1.0f, 1.0f);
    if (throttle < 0) turn *= (1.0f - throttle);

    // Angular velocity: decay 10/s, add yaw about local up and throttle pitch about local right.
    if (!t.dead) {
        Vec3 dYaw = f.localToWorld({0, dt * turn * turnRate, 0});
        Vec3 dPitch = f.localToWorld({dt * throttle * -2.0f, 0, 0});
        t.angVel = t.angVel * (1.0f - dt * k::kAngularDecay) + dYaw + dPitch;
    }

    // Nitro.
    float fwdAccel = m.maxFwdAccel, sideAccel = m.maxSideAccel;
    bool boosting = (t.nitroEngaged && t.nitroMeter > 0 && in.boost) || t.nitroForced;
    if (boosting) {
        if (!t.nitroForced) {
            t.nitroMeter = std::max(0.0f, t.nitroMeter - dt / t.nitroDuration);
        }
        if (t.nitroMeter > 0) {
            fwdAccel *= t.nitroMult;
            maxSpeed *= t.nitroMult;
            throttle = 1.0f;
        } else {
            t.nitroEngaged = false;  // 0x900b0 stops the nitro
        }
    }
    if (!t.nitroEngaged && t.nitroMeter < 1) {
        t.nitroMeter = std::min(1.0f, t.nitroMeter + dt * k::kNitroRefillPerSec);
    }

    // Gravity and speed-scaled downforce, in velocity space.
    Vec3 v = t.linVel;
    Vec3 vL = f.worldToLocal(v);
    float speed = length(vL);
    float g = -k::kDriveGravity * dt;
    v += f.up * (g * (std::fabs(speed) * 1.5f / maxSpeed));
    v += kWorldUp * g;

    fwdAccel *= std::fabs(throttle) * 0.5f + 0.5f;
    sideAccel *= std::fabs(strafe) * 0.5f + 0.5f;

    // Target local velocities.
    float tf = maxSpeed * throttle;
    if (throttle < 0) tf *= k::kReverseSpeedScale;
    float ts = maxSide * strafe;
    float mag = std::sqrt(tf * tf + ts * ts);
    float cap = std::max(maxSpeed, maxSide);
    if (maxSpeed < mag) {
        ts *= cap / mag;
        tf *= cap / std::sqrt(tf * tf + vL.x * vL.x);  // faithful: uses current lateral speed
    }

    // Slope-limited acceleration (no climbing faster past 25 deg).
    const float s25 = k::kSteepSin;
    float aF;
    if (throttle > 0) aF = (pitch < s25) ? (pitch > 0 ? fwdAccel * ((s25 - pitch) / s25) : fwdAccel) : 0;
    else if (throttle < 0) aF = (-s25 < pitch) ? (pitch < 0 ? fwdAccel * ((pitch + s25) / s25) : fwdAccel) : 0;
    else aF = fwdAccel;
    float aS;
    if (strafe > 0) aS = (roll < s25) ? (roll <= 0 ? sideAccel : sideAccel * ((s25 - roll) / s25)) : 0;
    else if (strafe < 0) aS = (-s25 < roll) ? (0 <= roll ? sideAccel : sideAccel * ((roll + s25) / s25)) : 0;
    else aS = sideAccel;

    if (t.airborne && !t.nitroForced && !t.nitroEngaged) { aS *= k::kAirControl; aF *= k::kAirControl; }

    float dvF = dt * aF, dvS = dt * aS;
    float eF = std::fabs(vL.z - tf) / maxSpeed;
    float eS = std::fabs(vL.x - ts) / maxSide;
    if (eF < 0.2f) dvF *= eF * 5.0f;  // ease into the target speed
    if (eS < 0.2f) dvS *= eS * 5.0f;
    if (tf < vL.z) { dvF = -dvF; if (throttle >= 0) dvF *= 0.5f; }
    if (!t.nitroEngaged && t.nitroOverspeedTimer > 0) {
        t.nitroOverspeedTimer -= dt;
        if (t.maxFwdSpeed < t.lastFwdSpeed) dvF -= t.nitroMult;  // bleed speed after a boost
    }
    if (ts < vL.x) dvS = -dvS;

    Vec3 dv = f.localToWorld({dvS, 0, dvF});
    if (t.tooSteep && dv.y > 0) dv.y = 0;
    v += dv;
    t.linVel = v;
    t.lastFwdSpeed = f.worldToLocal(v).z;

    if (f.pos.y < k::kOutOfWorldY) t.dead = true;  // takeDamage(10000)
}

void integrateBody(TankState& t, float dt) {
    t.linVel.y -= k::kBodyGravity * dt;
    t.frame.pos += t.linVel * dt;
    t.frame.rotate(t.angVel * dt);
}

void regenStep(TankState& t, const TankMotion& m, float dt, float regenDelayBonus) {
    t.sinceDamage += dt;
    float delay = std::max(0.0f, m.healthRegenDelay - regenDelayBonus);
    if (!t.dead && delay < t.sinceDamage && t.hp < t.hpMax) {
        t.hp = std::min(t.hpMax, t.hp + m.healthRegenAmount * dt);
    }
}

}  // namespace bzpsp
