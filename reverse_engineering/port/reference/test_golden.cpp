// Checks the reference against golden traces recorded by running the game's own code under
// emulation (see ../validation):
//
//   test_golden tank   <golden/tank_traces.txt>   [tolerance]   HoverTank drive 0x954a0
//   test_golden damage <golden/damage_traces.txt> [tolerance]   Vehicle::takeDamage 0x99be8
//
// Every step is replayed from its recorded `pre` state, so errors never accumulate: a field
// that differs means the reference computes that step differently from the PSP code.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "bzpsp_ref.h"

using namespace bzpsp;

namespace {

const char* kFieldNames[] = {
    "right.x", "right.y", "right.z", "up.x", "up.y", "up.z", "at.x", "at.y", "at.z",
    "pos.x", "pos.y", "pos.z", "vel.x", "vel.y", "vel.z", "omega.x", "omega.y", "omega.z",
    "probeT0", "probeT1", "probeT2", "probeT3", "probeIndex", "airborne", "tooSteep",
    "steerHold", "steerDir", "maxFwdSpeed", "nitroMeter", "nitroMult", "nitroDuration",
    "nitroEngaged", "nitroForced", "nitroOverspeedTimer", "lastFwdSpeed", "slowed",
    "slowFactor", "dead",
};
constexpr int kFields = sizeof(kFieldNames) / sizeof(kFieldNames[0]);

void load(TankState& t, const std::vector<float>& s) {
    t.frame.right = {s[0], s[1], s[2]};
    t.frame.up = {s[3], s[4], s[5]};
    t.frame.at = {s[6], s[7], s[8]};
    t.frame.pos = {s[9], s[10], s[11]};
    t.linVel = {s[12], s[13], s[14]};
    t.angVel = {s[15], s[16], s[17]};
    for (int k = 0; k < 4; ++k) t.probeT[k] = s[18 + k];
    t.probeIndex = int(s[22]);
    t.airborne = s[23] != 0;
    t.tooSteep = s[24] != 0;
    t.steerHold = s[25];
    t.steerDir = int(s[26]);
    t.maxFwdSpeed = s[27];
    t.nitroMeter = s[28];
    t.nitroMult = s[29];
    t.nitroDuration = s[30];
    t.nitroEngaged = s[31] != 0;
    t.nitroForced = s[32] != 0;
    t.nitroOverspeedTimer = s[33];
    t.lastFwdSpeed = s[34];
    t.slowed = s[35] != 0;
    t.slowFactor = s[36];
    t.dead = s[37] != 0;
}

std::vector<float> save(const TankState& t) {
    const Frame& f = t.frame;
    return {f.right.x, f.right.y, f.right.z, f.up.x, f.up.y, f.up.z, f.at.x, f.at.y, f.at.z,
            f.pos.x, f.pos.y, f.pos.z, t.linVel.x, t.linVel.y, t.linVel.z,
            t.angVel.x, t.angVel.y, t.angVel.z, t.probeT[0], t.probeT[1], t.probeT[2], t.probeT[3],
            float(t.probeIndex), float(t.airborne), float(t.tooSteep), t.steerHold, float(t.steerDir),
            t.maxFwdSpeed, t.nitroMeter, t.nitroMult, t.nitroDuration, float(t.nitroEngaged),
            float(t.nitroForced), t.nitroOverspeedTimer, t.lastFwdSpeed, float(t.slowed),
            t.slowFactor, float(t.dead)};
}

std::vector<float> parseFloats(const std::string& s) {
    std::vector<float> out;
    std::istringstream in(s);
    std::string tok;
    while (in >> tok) out.push_back(std::strtof(tok.c_str(), nullptr));
    return out;
}

// Plane through the origin tilted `slopeDeg` about z (matches validation/tank_traces.py Ground).
RaycastFn groundFor(float slopeDeg) {
    float a = slopeDeg * 3.14159265358979f / 180.0f;
    Vec3 n{-std::sin(a), std::cos(a), 0};
    return [n](const Vec3& from, const Vec3& to) -> float {
        float da = dot(n, from), db = dot(n, to);
        if (da < 0 || db > 0 || da == db) return -1;
        return da / (da - db);
    };
}

float g_tol = 2e-4f;  // absolute + relative; float32 rounding order differs from the PSP's

bool close(float got, float want) {
    if (std::isinf(want) || want > 1e30f) return got > 1e30f;  // miss sentinel
    float tol = g_tol + g_tol * std::fabs(want);
    return std::fabs(got - want) <= tol;
}

struct Stats { int ticks = 0, bad = 0; std::map<int, int> byField; std::string first; };

// ---- damage traces ----
const char* kDamageFields[] = {
    "hp", "hpMax", "sinceDamage", "invulnerable", "shieldActive", "shieldHp", "armorActive",
    "armorScale", "frozen", "dead", "swarmT0", "swarmT1", "swarmT2", "fusionT0", "fusionT1",
    "fusionT2", "swarmN", "fusionN",
};
constexpr int kDamageCount = sizeof(kDamageFields) / sizeof(kDamageFields[0]);

void loadDamage(TankState& t, DamageState& d, const std::vector<float>& s) {
    t.hp = s[0]; t.hpMax = s[1]; t.sinceDamage = s[2];
    d.invulnerable = s[3] != 0; d.shieldActive = s[4] != 0; d.shieldHp = s[5];
    d.armorActive = s[6] != 0; d.armorScale = s[7]; d.frozen = s[8] != 0; t.dead = s[9] != 0;
    for (int c = 0; c < 2; ++c) {
        for (int i = 0; i < 3; ++i) d.comboTimes[c][i] = s[10 + 3 * c + i];
        d.comboCount[c] = int(s[16 + c]);
    }
}

std::vector<float> saveDamage(const TankState& t, const DamageState& d) {
    return {t.hp, t.hpMax, t.sinceDamage, float(d.invulnerable), float(d.shieldActive), d.shieldHp,
            float(d.armorActive), d.armorScale, float(d.frozen), float(t.dead),
            d.comboTimes[0][0], d.comboTimes[0][1], d.comboTimes[0][2],
            d.comboTimes[1][0], d.comboTimes[1][1], d.comboTimes[1][2],
            float(d.comboCount[0]), float(d.comboCount[1])};
}

int runDamage(std::ifstream& file) {
    int bad = 0, total = 0;
    std::string line;
    while (std::getline(file, line)) {
        if (line.empty() || line[0] == '#') continue;
        size_t p1 = line.find(" | "), p2 = line.find(" | ", p1 + 3);
        std::istringstream head(line.substr(0, p1));
        std::string name;
        int hit, bullet;
        float now, damage;
        head >> name >> hit >> now >> damage >> bullet;
        std::vector<float> pre = parseFloats(line.substr(p1 + 3, p2 - p1 - 3));
        std::vector<float> post = parseFloats(line.substr(p2 + 3));
        if (int(pre.size()) != kDamageCount || int(post.size()) != kDamageCount) {
            std::fprintf(stderr, "bad line (fields %zu/%zu)\n", pre.size(), post.size());
            return 2;
        }
        TankState t;
        DamageState d;
        loadDamage(t, d, pre);
        takeDamage(t, d, damage, bullet, now);
        std::vector<float> got = saveDamage(t, d);
        ++total;
        std::string detail;
        for (int i = 0; i < kDamageCount; ++i) {
            if (close(got[i], post[i])) continue;
            char buf[160];
            std::snprintf(buf, sizeof buf, " %s got %.6g want %.6g;", kDamageFields[i], got[i], post[i]);
            detail += buf;
        }
        if (!detail.empty()) {
            ++bad;
            std::printf("%-20s hit %d FAIL:%s\n", name.c_str(), hit, detail.c_str());
        }
    }
    std::printf("damage: %d hits, %d mismatched\n", total, bad);
    return bad ? EXIT_FAILURE : EXIT_SUCCESS;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: test_golden tank|damage <traces.txt> [tolerance]\n");
        return 2;
    }
    const std::string mode = argv[1];
    const char* path = argv[2];
    if (argc > 3) g_tol = std::strtof(argv[3], nullptr);
    std::ifstream file(path);
    if (!file) { std::fprintf(stderr, "cannot open %s\n", path); return 2; }
    if (mode == "damage") return runDamage(file);
    if (mode != "tank") { std::fprintf(stderr, "unknown mode %s\n", mode.c_str()); return 2; }

    std::map<std::string, Stats> stats;
    std::vector<std::string> order;
    std::string line;
    while (std::getline(file, line)) {
        if (line.empty() || line[0] == '#') continue;
        size_t p1 = line.find(" | "), p2 = line.find(" | ", p1 + 3);
        std::istringstream head(line.substr(0, p1));
        std::string name, groundCode;
        int size, tick, boost;
        float dt, steer, throttle, strafe;
        head >> name >> size >> tick >> dt >> steer >> throttle >> strafe >> boost >> groundCode;
        std::vector<float> pre = parseFloats(line.substr(p1 + 3, p2 - p1 - 3));
        std::vector<float> post = parseFloats(line.substr(p2 + 3));
        if (int(pre.size()) != kFields || int(post.size()) != kFields) {
            std::fprintf(stderr, "bad line (fields %zu/%zu)\n", pre.size(), post.size());
            return 2;
        }
        float slope = std::strtof(groundCode.substr(groundCode.find(':') + 1).c_str(), nullptr);

        TankState t;
        t.size = TankSize(size);
        load(t, pre);
        TankInput in;
        in.steer = steer; in.throttle = throttle; in.strafe = strafe; in.boost = boost != 0;
        driveStep(t, kShippedTankMotion[size], in, dt, groundFor(slope));
        std::vector<float> got = save(t);

        if (!stats.count(name)) order.push_back(name);
        Stats& st = stats[name];
        ++st.ticks;
        bool bad = false;
        std::string detail;
        for (int i = 0; i < kFields; ++i) {
            if (close(got[i], post[i])) continue;
            bad = true;
            ++st.byField[i];
            char buf[160];
            std::snprintf(buf, sizeof buf, " %s got %.6g want %.6g;", kFieldNames[i], got[i], post[i]);
            detail += buf;
        }
        if (bad) {
            if (st.first.empty()) st.first = "tick " + std::to_string(tick) + ":" + detail;
            ++st.bad;
        }
    }

    int totalBad = 0;
    for (const std::string& name : order) {
        const Stats& st = stats[name];
        totalBad += st.bad;
        std::printf("%-22s %4d ticks  %4d mismatched  %s\n", name.c_str(), st.ticks, st.bad,
                    st.bad ? "FAIL" : "ok");
        if (st.bad) {
            std::printf("    first %s\n    fields:", st.first.c_str());
            for (auto& kv : st.byField) std::printf(" %s x%d", kFieldNames[kv.first], kv.second);
            std::printf("\n");
        }
    }
    std::printf("\n%d mismatched tick(s)\n", totalBad);
    return totalBad ? EXIT_FAILURE : EXIT_SUCCESS;
}
