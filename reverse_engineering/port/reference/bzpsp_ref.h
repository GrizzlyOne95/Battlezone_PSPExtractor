// Battlezone PSP gameplay reference implementation (engine-neutral C++17).
//
// Transcribed from the decompiled BOOT.BIN (addresses are static VAs, PRX base 0).
// Purpose: a port (Unreal, BZ98 Redux DLL, ...) can call or copy these routines and get the
// same numbers the PSP game produces. Engine-specific parts (raycasts, collision response,
// rendering, audio) are left to the host through small callbacks.
//
// Coordinate frame: RenderWare, right-handed, +Y up, meters, seconds.
// A body's rotation is stored as three rows: right (+X local), up (+Y), at (+Z, forward).
// See PORT_SPEC.md for Unreal / BZ98 Redux axis conversion.
#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <functional>
#include <vector>

namespace bzpsp {

// ---------------------------------------------------------------------------------------------
// Math

struct Vec3 {
    float x = 0, y = 0, z = 0;
    Vec3() = default;
    Vec3(float X, float Y, float Z) : x(X), y(Y), z(Z) {}
    Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    Vec3 operator*(float s) const { return {x * s, y * s, z * s}; }
    Vec3& operator+=(const Vec3& o) { x += o.x; y += o.y; z += o.z; return *this; }
    Vec3& operator-=(const Vec3& o) { x -= o.x; y -= o.y; z -= o.z; return *this; }
};
inline float dot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(const Vec3& a, const Vec3& b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline float length(const Vec3& v) { return std::sqrt(dot(v, v)); }
inline Vec3 normalize(const Vec3& v) { float l = length(v); return l > 0 ? v * (1.0f / l) : v; }

// Rotation as RenderWare frame rows.
struct Frame {
    Vec3 right{1, 0, 0}, up{0, 1, 0}, at{0, 0, 1}, pos{};
    Vec3 localToWorld(const Vec3& v) const { return right * v.x + up * v.y + at * v.z; }   // 0x22330c with R
    Vec3 worldToLocal(const Vec3& v) const { return {dot(v, right), dot(v, up), dot(v, at)}; }  // with R^T
    Vec3 transformPoint(const Vec3& v) const { return pos + localToWorld(v); }            // 0x1b5d6c
    void rotate(const Vec3& worldAxisAngleRad);                                          // integrate w*dt
    void orthonormalize();
};

const Vec3 kWorldUp{0, 1, 0};  // .data 0x24b7a8

// ---------------------------------------------------------------------------------------------
// Tables (values come from BZ_TANK_MOTION.CSV / BZ_ENHANCE_DEFS.CSV / BZ_WEAP_DEFS / BZ_PROJ_DEFS)

enum TankSize { kSmall = 0, kMedium = 1, kLarge = 2 };

struct TankMotion {            // one column of BZ_TANK_MOTION.CSV
    float turnRate, maxFwdSpeed, maxFwdAccel, maxSideSpeed, maxSideAccel;
    float hitPoints, energyPoints, energyRecharge;
    float suspensionHeight, hoverDampCoeff, suspensionGain, uprightGain;
    float energyStart, killEnergyBonus, healthRegenDelay, healthRegenAmount;
    float deathSplashRadius, deathSplashDamage, deathShakeDur, deathShakeMag;
};
// Shipped CSV values (US disc). Compiled-in defaults in .data differ and are overwritten at boot.
extern const TankMotion kShippedTankMotion[3];

enum Enhance {                 // HoverTank dump enum order (strings at 0x23da4c)
    eTopSpeed, eNitroBoost, eChargeEnergy, eIncreasedPickup, eChargeHitPointTime,
    eVampireDamage, eIncreasedLockon, eIncreasedDamage, eIncreasedSplashDamage, eFastTeamSpecial,
    kEnhanceCount
};
struct EnhanceValue { float major, minor; };
extern const EnhanceValue kShippedEnhance[kEnhanceCount];

// ---------------------------------------------------------------------------------------------
// Constants recovered from code (see PORT_SPEC.md for the address of each)

namespace k {
constexpr float kMaxDt = 1.0f / 15.0f;          // 0x8f43c clamps the tank step dt
constexpr float kMass = 100.0f;                 // 0x8f830 rigid body mass
constexpr float kInertia = 300.0f;              // 0x8f830 isotropic inertia
constexpr float kBodyGravity = 30.0f;           // 0x8f830 per-body gravity (0,-30,0) in the solver
constexpr float kDriveGravity = 9.8f;           // 0x954a0 extra gravity added by the drive model
constexpr float kSteepSin = 0.42261826f;        // sin(25 deg), 0x954a0
constexpr float kAngularDecay = 10.0f;          // w *= (1 - 10 dt)
constexpr float kReverseSpeedScale = 0.75f;
constexpr float kAirControl = 0.25f;
constexpr float kNitroRefillPerSec = 0.125f;
constexpr float kOutOfWorldY = -225.0f;
constexpr float kHoverTorqueScale = 0.4f;
const Vec3 kHoverProbes[4] = {{2, 0, 2}, {-2, 0, 2}, {2, 0, -2}, {-2, 0, -2}};  // .data 0x24e0ac
}  // namespace k

// ---------------------------------------------------------------------------------------------
// Hover tank

struct TankInput {
    float steer = 0;     // input +0x284, -1..1
    float throttle = 0;  // input +0x288, -1..1 (negative = reverse)
    float strafe = 0;    // input +0x28c, -1..1
    bool boost = false;  // input +0x294
};

// Host raycast: segment from `from` to `to`; returns hit fraction in [0,1] or a negative value.
using RaycastFn = std::function<float(const Vec3& from, const Vec3& to)>;

struct TankState {
    TankSize size = kMedium;
    Frame frame;
    Vec3 linVel, angVel;               // world space (body +0x90 / +0x150)
    // hover
    std::array<float, 4> probeT{{0, 0, 0, 0}};  // tank +0x85c (stride 16); 0 until first update
    int probeIndex = 0;                // tank +0x890
    bool airborne = false;             // tank +0x5a8
    bool tooSteep = false;             // tank +0x5ac
    // steering ramp
    float steerHold = 0;               // tank +0x5bc
    int steerDir = 0;                  // tank +0x5b8 (0 = right/positive, 1 = negative)
    // speed / nitro
    float maxFwdSpeed = 0;             // tank +0x5b4 (table + TopSpeed tweak)
    float nitroMeter = 1;              // tank +0x828 (0..1)
    float nitroMult = 1;               // tank +0x824
    float nitroDuration = 1;           // tank +0x834
    bool nitroEngaged = false;         // tank +0x82c
    bool nitroForced = false;          // tank +0x944 (e.g. Super Ram)
    float nitroOverspeedTimer = 0;     // tank +0x838
    float lastFwdSpeed = 0;            // tank +0x5b0
    bool slowed = false;               // tank +0x840 (lockdown/EMP)
    float slowFactor = 1;              // tank +0x844
    bool dead = false;
    // health / energy
    float hp = 0, hpMax = 0;           // vehicle +0x20 / +0x24
    float energy = 0, energyMax = 0;   // vehicle +0x4c / +0x50
    float sinceDamage = 0;             // vehicle +0x28
};

struct Impulse { Vec3 linear, angular; };  // what 0xe11bc receives (v += J/m, L += tau)

// Initialize a tank at spawn (0x9326c): HP, energy, speed from the motion table + tweaks.
void spawnTank(TankState& t, const TankMotion& m, float topSpeedBonus);

// Hover suspension for one tick (0x94da0). Returns the impulse to apply to the body.
Impulse hoverStep(TankState& t, const TankMotion& m, float dt, const RaycastFn& raycast);

// Full HoverTank drive update (0x954a0): calls hoverStep, then rewrites angVel and linVel.
// Apply the returned hover impulse before or after as your integrator prefers; the PSP
// applies it inside the same call, before reading the velocity (see PORT_SPEC.md).
void driveStep(TankState& t, const TankMotion& m, const TankInput& in, float dt, const RaycastFn& raycast);

// Minimal body integration used by the tests (not the PSP physics library):
// v -= 30 * dt (body gravity), pos += v*dt, rotate by w*dt.
void integrateBody(TankState& t, float dt);

// Health regeneration (0x97b30 / 0x9326c fields).
void regenStep(TankState& t, const TankMotion& m, float dt, float regenDelayBonus);

// ---------------------------------------------------------------------------------------------
// Combat

struct DamageState {
    bool invulnerable = false;         // vehicle +0x2c
    bool armorActive = false;          // vehicle +0x74 (Germany special)
    float armorScale = 1;              // vehicle +0x78
    bool shieldActive = false;         // vehicle +0x60 (Armor pickup)
    float shieldHp = 0;                // vehicle +0x64
    // 3-hit combo tracker (0x98808): per bullet id 14 (Swarm) and 8 (Fusion)
    std::array<std::array<float, 3>, 2> comboTimes{};
    std::array<int, 2> comboCount{{0, 0}};
};

// Vehicle::takeDamage core (0x99be8 + 0x98808). `bulletId` is the projectile def id (or -1),
// `now` the match clock. Returns the damage actually removed from HP; sets t.dead at <= 0.
float takeDamage(TankState& t, DamageState& d, float damage, int bulletId, float now);

// Linear splash falloff used by explosions (0x64e8): damage * (1 - dist/radius) inside radius.
float splashDamageAt(float damage, float radius, float dist);

struct ProjectileDef {  // BZ_PROJ_DEFS.CSV row (subset used by the reference)
    int bulletId, type;
    float lifeSpan, velocity, randVelSlow, damage, impactForce;
    float stealScale, burnDamage, burnMaxTime, maxTurnRate, explodRadius, lockDelay;
};

struct Projectile {
    const ProjectileDef* def = nullptr;
    Frame frame;
    Vec3 velPerUpdate;   // +0x60: translation applied every update (NOT scaled by dt)
    float age = 0, damage = 0, steal = 0;
    int targetId = -1;   // missile target
    float lockTimer = 0;
};

// Spawn (0x7328): speed = velocity + 5% of shooter speed (random slow not modeled here).
Projectile spawnProjectile(const ProjectileDef& def, const Frame& muzzle, float shooterSpeed,
                           float weaponDamageBonus, float weaponStealBonus);

// Missile steering for one update (0xb998). `targetPos/targetVel` are world values.
void steerMissile(Projectile& p, const Vec3& targetPos, const Vec3& targetVel, float dt);

// Advance position; returns false when the projectile expired (age >= lifeSpan).
bool advanceProjectile(Projectile& p, float dt);

// ---------------------------------------------------------------------------------------------
// Match rules

enum Mode { kDM = 0, kTDM = 1, kCTF = 2, kFAH = 3, kHZ = 4, kKO = 5 };
enum MatchState { kIntro = 0, kCountdown = 1, kPlaying = 2, kGameOver = 3 };

struct Player { int team = 0; int score = 0; bool alive = true; };

struct Match {
    Mode mode = kDM;
    MatchState state = kIntro;
    bool singlePlayer = true;
    float timeLimit = 0;        // seconds, 0 = none (+0x30/+0x34)
    float originalTimeLimit = 0;// +0x38
    int scoreLimit = 0;         // 0 = none (+0x24/+0x28)
    float clock = 0;            // +0x2c
    float stateTimer = 0;       // +0x3c / +0x40
    bool overtime = false;      // +0x44
    int teamScore[2] = {0, 0};  // +0xd8 (0 = red, 1 = blue)
    std::vector<Player> players;
    int winner = -1;            // player index (FFA) or team (team modes)

    bool teamGame() const { return mode == kTDM || mode == kCTF || mode == kHZ || mode == kKO; }
    void addPlayerScore(int p, int delta);   // 0x51010 (clamped at 0)
    void addTeamScore(int team, int delta);  // 0x46544
    bool checkScoreLimit(int who);           // mode-specific win on reaching the limit
    // GameType::update clock handling (0x45564). `pressedStart` ends the intro.
    void update(float dt, bool pressedStart);
    // Common kill scoring (per-mode handlers 0x3bc74 / 0x3e0d8 / 0x3b954 / 0x3dc04 / 0x3c828 / 0x3d474)
    void onKill(int victim, int killer, bool victimCarriedObjective);
};

// Respawn delays (0x44b6c): the human is forced back after 35 s on the Respawn screen, AI after 4 s.
constexpr float kHumanRespawnTimeout = 35.0f;
constexpr float kAiRespawnDelay = 4.0f;

// CTF flag (0x2e598 family). Distances in meters; radius 20 m (dist^2 < 400).
struct CtfFlag {
    int team = 0;
    int state = 0;             // 0 home, 1 dropped, 2 carried
    int carrier = -1;
    float droppedTime = 0;
    static constexpr float kRadius = 20.0f;
    static constexpr float kAutoReturn = 20.0f;   // .bss 0x303494 set at 0x2ef20
};

// Hot Zone pad (0x313a4) and team scoring (0x3c0ac / 0x3c604).
struct HzPad {
    int owner = -1;            // team, -1 neutral
    int capturer = -1;         // tank index currently capturing
    float checkTimer = 0, captureTimer = 0;
    static constexpr float kRadius = 30.0f;       // dist^2 < 900
    static constexpr float kCaptureTime = 3.0f;
    static constexpr float kCheckInterval = 1.0f;
};
// Seconds per team point by number of pads held (0x3c604 local table {0,3,2,1}).
constexpr float kHzPointInterval[4] = {0.0f, 3.0f, 2.0f, 1.0f};

// Knockout core / charge pad (0x34430, 0x34594, 0x32290).
struct KoCore {
    float health = 100.0f;     // .data 0x24b764
    float shield = 1600.0f;    // .data 0x24b768
    bool shieldUp = true;
    bool destroyed = false;
    // Apply damage; returns true if this hit destroyed the core.
    bool takeDamage(float dmg);
    // Charge pad tick: each qualifying teammate pays 10 energy for +20 health (1 s ticks, 20 m).
    void heal(float amount);
};
constexpr float kKoPadRadius = 20.0f, kKoPadTick = 1.0f, kKoPadCost = 10.0f, kKoPadRatio = 2.0f;

}  // namespace bzpsp
