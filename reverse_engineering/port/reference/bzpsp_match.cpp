// Match clock and scoring (GameType 0x45564 and the six per-mode kill handlers).
#include <algorithm>

#include "bzpsp_ref.h"

namespace bzpsp {

void Match::addPlayerScore(int p, int delta) {
    players[p].score = std::max(0, players[p].score + delta);
}

void Match::addTeamScore(int team, int delta) { teamScore[team] += delta; }

bool Match::checkScoreLimit(int who) {
    if (scoreLimit <= 0 || state == kGameOver) return false;
    int score = teamGame() ? teamScore[who] : players[who].score;
    if (score >= scoreLimit) {
        winner = who;
        state = kGameOver;
        return true;
    }
    return false;
}

void Match::update(float dt, bool pressedStart) {
    switch (state) {
    case kIntro:  // single player: 5 s, then wait for a button
        stateTimer += dt;
        if (stateTimer > 5.0f && pressedStart) { state = kCountdown; stateTimer = 0; }
        return;
    case kCountdown:
        stateTimer += dt;
        if (stateTimer > 3.0f) state = kPlaying;
        return;
    case kGameOver:
        return;
    case kPlaying:
        break;
    }
    clock += dt;
    if (timeLimit <= 0 || clock < timeLimit) return;

    // Time is up: decide or go to overtime (0x3ea18).
    bool decided;
    if (!teamGame()) {
        int best = -1, bestScore = -1, tied = 0;
        for (size_t i = 0; i < players.size(); ++i) {
            if (players[i].score > bestScore) { bestScore = players[i].score; best = int(i); tied = 1; }
            else if (players[i].score == bestScore) ++tied;
        }
        decided = tied < 2;
        if (decided) winner = best;
    } else {
        decided = teamScore[0] != teamScore[1];
        if (decided) winner = teamScore[1] > teamScore[0] ? 1 : 0;
    }
    if (decided) { state = kGameOver; return; }
    float extra = std::max(originalTimeLimit * 0.1f, 60.0f);
    overtime = true;
    timeLimit += extra;
}

void Match::onKill(int victim, int killer, bool victimCarriedObjective) {
    if (killer < 0) return;                    // environment kill: message only
    bool suicide = victim == killer;
    bool sameTeam = teamGame() && players[victim].team == players[killer].team;
    int team = players[killer].team;

    if (suicide || sameTeam) {
        addPlayerScore(killer, -1);
        if (mode == kTDM) addTeamScore(team, -1);
        return;
    }
    bool bonus = victimCarriedObjective && (mode == kCTF || mode == kFAH);
    addPlayerScore(killer, bonus ? 5 : 1);
    if (mode == kTDM) { addTeamScore(team, 1); checkScoreLimit(team); }
    if (mode == kDM || mode == kFAH) checkScoreLimit(killer);
}

bool KoCore::takeDamage(float dmg) {
    if (destroyed) return false;
    if (shieldUp) {
        shield -= dmg;
        if (shield <= 0) { shield = 0; shieldUp = false; }
        return false;
    }
    health -= dmg;
    if (health <= 0) { health = 0; destroyed = true; return true; }
    return false;
}

void KoCore::heal(float amount) {
    if (amount <= 0) return;
    if (destroyed) destroyed = false;  // pad online again (0x344bc)
    health = std::min(100.0f, health + amount);
}

}  // namespace bzpsp
