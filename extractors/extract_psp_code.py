#!/usr/bin/env python3
"""
Static code analysis for the Battlezone PSP executable (PSP_GAME/SYSDIR/BOOT.BIN).

BOOT.BIN on the Battlezone UMD is an unencrypted, stripped PSP PRX (ELF type 0xFFA0)
that still carries its PSP relocation sections (type 0x700000A0). Those relocations
let every `lui`/`addiu` address pair and every data pointer be resolved exactly, so
this tool can rebuild a useful map of the program without a disassembler GUI:

- module info, imported libraries and functions (NIDs resolved by hashing known names)
- relocation-resolved code->data references, call graph and data pointer tables
- function discovery (call targets, callbacks, vtable slots, tail-call targets)
- strings with the functions that reference them
- source-file attribution from leaked assert paths (c:/Trees/Psp/Game/Source/*.cpp)
- automatic names from debug strings ("HoverTank::readMotionDefFile", "NetGameTick", ...)
- vtable / function-pointer table candidates
- Ghidra ImportSymbolsScript.py compatible symbol file
- optional annotated disassembly listing (requires capstone)

Input may be BOOT.BIN / an ELF PRX directly, an extracted disc folder (ISO root,
PSP_GAME or SYSDIR) or an .iso image. Encrypted "~PSP" EBOOT.BIN files are detected
and reported; use BOOT.BIN instead.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ELF_MAGIC = b"\x7fELF"
PSP_ENCRYPTED_MAGIC = b"~PSP"
SHT_PSP_REL = 0x700000A0

R_MIPS_32 = 2
R_MIPS_26 = 4
R_MIPS_HI16 = 5
R_MIPS_LO16 = 6

REG_NAMES = (
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
)

# Module-level exports use fixed NIDs.
MODULE_EXPORT_NIDS = {
    0xD632ACDB: "module_start",
    0xCEE8593C: "module_stop",
    0xF01D73A7: "module_info",
    0x0F7C276C: "module_start_thread_parameter",
    0xCF0CC697: "module_stop_thread_parameter",
    0xD3744BE0: "module_bootstart",
    0x2F064FA6: "module_reboot_before",
    0x11B97506: "module_sdk_version",
}

# User-mode library NIDs are the first four bytes (little endian) of SHA-1(name),
# so candidate names are verified by hashing: a wrong guess can never mislabel a NID.
KNOWN_IMPORT_NAMES = """
sceRtcGetCurrentTick sceRtcGetTickResolution sceRtcGetCurrentClock sceRtcGetCurrentClockLocalTime
sceRtcGetDaysInMonth sceRtcGetDayOfWeek sceRtcSetTick sceRtcGetTick sceRtcTickAddSeconds sceRtcCompareTick
sceRtcConvertUtcToLocalTime sceRtcConvertLocalTimeToUTC sceRtcIsLeapYear sceRtcCheckValid sceRtcSetTime_t
sceRtcGetTime_t sceRtcFormatRFC3339 sceRtcTickAddMicroseconds sceRtcTickAddTicks
sceWlanGetSwitchState sceWlanGetEtherAddr sceWlanDevIsPowerOn
sceUtilitySavedataInitStart sceUtilitySavedataGetStatus sceUtilitySavedataUpdate sceUtilitySavedataShutdownStart
sceUtilityMsgDialogInitStart sceUtilityMsgDialogGetStatus sceUtilityMsgDialogUpdate sceUtilityMsgDialogShutdownStart
sceUtilityMsgDialogAbort sceUtilityOskInitStart sceUtilityOskGetStatus sceUtilityOskUpdate sceUtilityOskShutdownStart
sceUtilityGetSystemParamInt sceUtilityGetSystemParamString sceUtilitySetSystemParamInt sceUtilitySetSystemParamString
sceUtilityNetconfInitStart sceUtilityNetconfGetStatus sceUtilityNetconfUpdate sceUtilityNetconfShutdownStart
sceUtilityLoadNetModule sceUtilityUnloadNetModule sceUtilityLoadAvModule sceUtilityUnloadAvModule
sceUtilityLoadModule sceUtilityUnloadModule sceUtilityLoadUsbModule sceUtilityUnloadUsbModule
sceUtilityGameSharingInitStart sceUtilityGameSharingGetStatus sceUtilityGameSharingUpdate
sceUtilityGameSharingShutdownStart sceUtilityCheckNetParam sceUtilityGetNetParam
sceAtracSetDataAndGetID sceAtracGetSoundSample sceAtracReleaseAtracID sceAtracDecodeData sceAtracGetRemainFrame
sceAtracGetStreamDataInfo sceAtracAddStreamData sceAtracSetLoopNum sceAtracGetNextSample sceAtracGetMaxSample
sceAtracResetPlayPosition sceAtracGetBufferInfoForReseting sceAtracGetSecondBufferInfo sceAtracSetSecondBuffer
sceAtracGetNextDecodePosition sceAtracGetChannel sceAtracGetBitrate sceAtracGetLoopStatus sceAtracGetOutputChannel
sceAtracSetData sceAtracGetAtracID sceAtracSetHalfwayBufferAndGetID sceAtracGetInternalErrorInfo
sceAtracSetHalfwayBuffer sceAtracStartEntry sceAtracEndEntry sceAtracGetSecondBufferInfo sceAtracReinit
__sceSasInit __sceSasCore __sceSasCoreWithMix __sceSasSetADSR __sceSasSetADSRmode __sceSasSetKeyOff
__sceSasSetKeyOn __sceSasSetPitch __sceSasSetVoice __sceSasSetVolume __sceSasSetSL __sceSasSetSimpleADSR
__sceSasSetNoise __sceSasGetEndFlag __sceSasGetEnvelopeHeight __sceSasRevType __sceSasRevParam __sceSasRevEVOL
__sceSasRevVON __sceSasSetGrain __sceSasGetGrain __sceSasSetOutputmode __sceSasGetOutputmode __sceSasGetPauseFlag
__sceSasSetPause __sceSasGetAllEnvelopeHeights __sceSasSetVoicePCM __sceSasSetTrianglarWave __sceSasSetSteepWave
sceAudioChReserve sceAudioChRelease sceAudioOutput sceAudioOutputBlocking sceAudioOutputPanned
sceAudioOutputPannedBlocking sceAudioChangeChannelVolume sceAudioChangeChannelConfig sceAudioSetChannelDataLen
sceAudioGetChannelRestLen sceAudioGetChannelRestLength sceAudioSRCChReserve sceAudioSRCChRelease
sceAudioSRCOutputBlocking sceAudioOutput2Reserve sceAudioOutput2Release sceAudioOutput2OutputBlocking
sceAudioOneshotOutput sceAudioInputBlocking sceAudioInput sceAudioInputInit sceAudioGetInputLength
sceCtrlSetSamplingCycle sceCtrlGetSamplingCycle sceCtrlSetSamplingMode sceCtrlGetSamplingMode
sceCtrlReadBufferPositive sceCtrlPeekBufferPositive sceCtrlReadBufferNegative sceCtrlPeekBufferNegative
sceCtrlReadLatch sceCtrlPeekLatch sceCtrlSetIdleCancelThreshold sceCtrlGetIdleCancelThreshold
sceDisplaySetMode sceDisplayGetMode sceDisplaySetFrameBuf sceDisplayGetFrameBuf sceDisplayWaitVblankStart
sceDisplayWaitVblankStartCB sceDisplayWaitVblank sceDisplayWaitVblankCB sceDisplayGetVcount sceDisplayIsVblank
sceDisplayGetFramePerSec sceDisplayGetCurrentHcount sceDisplayGetAccumulatedHcount sceDisplayIsForeground
sceDmacMemcpy sceDmacTryMemcpy
sceGeEdramGetAddr sceGeEdramGetSize sceGeListEnQueue sceGeListEnQueueHead sceGeListDeQueue
sceGeListUpdateStallAddr sceGeListSync sceGeDrawSync sceGeSetCallback sceGeUnsetCallback sceGeSaveContext
sceGeRestoreContext sceGeGetMtx sceGeGetCmd sceGeBreak sceGeContinue sceGeEdramSetAddrTranslation
sceKernelDcacheWritebackAll sceKernelDcacheWritebackInvalidateAll sceKernelDcacheWritebackRange
sceKernelDcacheWritebackInvalidateRange sceKernelDcacheInvalidateRange sceKernelIcacheInvalidateAll
sceKernelIcacheInvalidateRange sceKernelLibcTime sceKernelLibcClock sceKernelLibcGettimeofday sceKernelGetGPI
sceKernelSetGPO sceKernelUtilsMt19937Init sceKernelUtilsMt19937UInt sceKernelUtilsMd5Digest
sceKernelUtilsSha1Digest sceKernelUtilsMd5BlockInit sceKernelUtilsMd5BlockUpdate sceKernelUtilsMd5BlockResult
sceKernelCreateThread sceKernelStartThread sceKernelExitThread sceKernelExitDeleteThread sceKernelDeleteThread
sceKernelTerminateThread sceKernelTerminateDeleteThread sceKernelSleepThread sceKernelSleepThreadCB
sceKernelWakeupThread sceKernelCancelWakeupThread sceKernelDelayThread sceKernelDelayThreadCB
sceKernelDelaySysClockThread sceKernelDelaySysClockThreadCB sceKernelWaitThreadEnd sceKernelWaitThreadEndCB
sceKernelGetThreadId sceKernelChangeThreadPriority sceKernelChangeCurrentThreadAttr sceKernelRotateThreadReadyQueue
sceKernelReferThreadStatus sceKernelReferThreadRunStatus sceKernelGetThreadCurrentPriority
sceKernelGetThreadExitStatus sceKernelGetThreadStackFreeSize sceKernelCheckThreadStack sceKernelSuspendThread
sceKernelResumeThread sceKernelSuspendDispatchThread sceKernelResumeDispatchThread sceKernelReleaseWaitThread
sceKernelCreateSema sceKernelDeleteSema sceKernelSignalSema sceKernelWaitSema sceKernelWaitSemaCB
sceKernelPollSema sceKernelCancelSema sceKernelReferSemaStatus sceKernelCreateEventFlag sceKernelDeleteEventFlag
sceKernelSetEventFlag sceKernelClearEventFlag sceKernelWaitEventFlag sceKernelWaitEventFlagCB
sceKernelPollEventFlag sceKernelCancelEventFlag sceKernelReferEventFlagStatus sceKernelCreateCallback
sceKernelDeleteCallback sceKernelNotifyCallback sceKernelCancelCallback sceKernelGetCallbackCount
sceKernelCheckCallback sceKernelReferCallbackStatus sceKernelGetSystemTimeLow sceKernelGetSystemTimeWide
sceKernelGetSystemTime sceKernelUSec2SysClock sceKernelSysClock2USec sceKernelUSec2SysClockWide
sceKernelSysClock2USecWide sceKernelCreateMbx sceKernelDeleteMbx sceKernelSendMbx sceKernelReceiveMbx
sceKernelReceiveMbxCB sceKernelPollMbx sceKernelCancelReceiveMbx sceKernelReferMbxStatus sceKernelCreateMsgPipe
sceKernelDeleteMsgPipe sceKernelSendMsgPipe sceKernelSendMsgPipeCB sceKernelTrySendMsgPipe
sceKernelReceiveMsgPipe sceKernelReceiveMsgPipeCB sceKernelTryReceiveMsgPipe sceKernelCreateFpl
sceKernelDeleteFpl sceKernelAllocateFpl sceKernelAllocateFplCB sceKernelTryAllocateFpl sceKernelFreeFpl
sceKernelCreateVpl sceKernelDeleteVpl sceKernelAllocateVpl sceKernelAllocateVplCB sceKernelTryAllocateVpl
sceKernelFreeVpl sceKernelSetAlarm sceKernelSetSysClockAlarm sceKernelCancelAlarm sceKernelReferAlarmStatus
sceKernelSetVTimerHandler sceKernelCreateVTimer sceKernelStartVTimer sceKernelStopVTimer
sceKernelGetThreadmanIdList sceKernelReferSystemStatus sceKernelReferThreadProfiler
sceKernelAllocPartitionMemory sceKernelFreePartitionMemory sceKernelGetBlockHeadAddr sceKernelTotalFreeMemSize
sceKernelMaxFreeMemSize sceKernelDevkitVersion sceKernelPrintf sceKernelSetCompiledSdkVersion
sceKernelSetCompilerVersion sceKernelGetCompiledSdkVersion sceKernelQueryMemoryInfo
sceKernelStdin sceKernelStdout sceKernelStderr sceKernelStdioRead sceKernelStdioWrite sceKernelStdioOpen
sceKernelStdioClose sceKernelStdioLseek
sceKernelPowerLock sceKernelPowerUnlock sceKernelPowerTick sceKernelVolatileMemLock sceKernelVolatileMemTryLock
sceKernelVolatileMemUnlock
sceKernelLoadModule sceKernelStartModule sceKernelStopModule sceKernelUnloadModule sceKernelSelfStopUnloadModule
sceKernelStopUnloadSelfModule sceKernelGetModuleIdByAddress sceKernelGetModuleId sceKernelLoadModuleByID
sceKernelQueryModuleInfo sceKernelGetModuleIdList sceKernelLoadModuleMs sceKernelLoadModuleBufferUsbWlan
sceKernelExitGame sceKernelRegisterExitCallback sceKernelLoadExec sceKernelExitGameWithStatus
sceKernelCpuSuspendIntr sceKernelCpuResumeIntr sceKernelCpuResumeIntrWithSync sceKernelIsCpuIntrEnable
sceKernelIsCpuIntrSuspended sceKernelLockLwMutex sceKernelUnlockLwMutex sceKernelTryLockLwMutex
sceKernelLockLwMutexCB sceKernelReferLwMutexStatus
sceIoOpen sceIoOpenAsync sceIoClose sceIoCloseAsync sceIoRead sceIoReadAsync sceIoWrite sceIoWriteAsync
sceIoLseek sceIoLseekAsync sceIoLseek32 sceIoLseek32Async sceIoRemove sceIoMkdir sceIoRmdir sceIoChdir
sceIoRename sceIoDopen sceIoDread sceIoDclose sceIoDevctl sceIoIoctl sceIoIoctlAsync sceIoGetstat sceIoChstat
sceIoWaitAsync sceIoWaitAsyncCB sceIoPollAsync sceIoGetAsyncStat sceIoCancel sceIoGetDevType sceIoAssign
sceIoUnassign sceIoSync sceIoSetAsyncCallback sceIoChangeAsyncPriority sceIoGetFdList
sceUmdCheckMedium sceUmdActivate sceUmdDeactivate sceUmdWaitDriveStat sceUmdWaitDriveStatWithTimer
sceUmdWaitDriveStatCB sceUmdCancelWaitDriveStat sceUmdGetDriveStat sceUmdGetErrorStat sceUmdGetDiscInfo
sceUmdRegisterUMDCallBack sceUmdUnRegisterUMDCallBack sceUmdReplacePermit sceUmdReplaceProhibit
scePowerRegisterCallback scePowerUnregisterCallback scePowerUnregitserCallback scePowerGetBatteryLifePercent
scePowerIsBatteryCharging scePowerSetClockFrequency scePowerSetCpuClockFrequency scePowerSetBusClockFrequency
scePowerGetCpuClockFrequency scePowerGetBusClockFrequency scePowerIsPowerOnline scePowerIsLowBattery
scePowerTick scePowerLock scePowerUnlock scePowerGetBatteryLifeTime scePowerIsBatteryExist
scePsmfSetPsmf scePsmfGetNumberOfStreams scePsmfGetNumberOfSpecificStreams scePsmfSpecifyStream
scePsmfSpecifyStreamWithStreamType scePsmfSpecifyStreamWithStreamTypeNumber scePsmfGetCurrentStreamType
scePsmfGetCurrentStreamNumber scePsmfGetStreamSize scePsmfQueryStreamOffset scePsmfQueryStreamSize
scePsmfGetHeaderSize scePsmfGetPresentationStartTime scePsmfGetPresentationEndTime scePsmfGetVideoInfo
scePsmfGetAudioInfo scePsmfGetNumberOfEPentries scePsmfGetEPWithId scePsmfGetEPWithTimestamp
scePsmfGetEPidWithTimestamp scePsmfVerifyPsmf scePsmfCheckEPMap scePsmfGetPsmfVersion scePsmfGetPsmfMark
scePsmfGetInfoEP scePsmfGetStreamSize
sceMpegInit sceMpegFinish sceMpegQueryMemSize sceMpegCreate sceMpegDelete sceMpegRegistStream
sceMpegUnRegistStream sceMpegMallocAvcEsBuf sceMpegFreeAvcEsBuf sceMpegQueryAtracEsSize sceMpegQueryPcmEsSize
sceMpegInitAu sceMpegGetAvcAu sceMpegGetAtracAu sceMpegGetPcmAu sceMpegAvcDecode sceMpegAvcDecodeStop
sceMpegAvcDecodeFlush sceMpegAvcDecodeDetail sceMpegAvcDecodeMode sceMpegAtracDecode
sceMpegRingbufferQueryMemSize sceMpegRingbufferConstruct sceMpegRingbufferDestruct sceMpegRingbufferPut
sceMpegRingbufferAvailableSize sceMpegQueryStreamOffset sceMpegQueryStreamSize sceMpegFlushAllStream
sceMpegFlushStream sceMpegChangeGetAuMode sceMpegChangeGetAvcAuMode sceMpegAvcDecodeYCbCr sceMpegAvcCsc
sceMpegQueryUserdataEsSize sceMpegNextAvcRpAu sceMpegGetAvcNalAu sceMpegAvcDecodeStopYCbCr sceMpegGetAvcEsAu
sceMpegAvcDecodeDetail2 sceMpegAvcQueryYCbCrSize sceMpegAvcInitYCbCr sceMpegAvcCopyYCbCr
sceNetInit sceNetTerm sceNetFreeThreadinfo sceNetThreadAbort sceNetEtherNtostr sceNetEtherStrton
sceNetGetLocalEtherAddr sceNetGetMallocStat
sceNetAdhocctlInit sceNetAdhocctlTerm sceNetAdhocctlConnect sceNetAdhocctlCreate sceNetAdhocctlJoin
sceNetAdhocctlScan sceNetAdhocctlDisconnect sceNetAdhocctlAddHandler sceNetAdhocctlDelHandler
sceNetAdhocctlGetState sceNetAdhocctlGetAdhocId sceNetAdhocctlGetPeerList sceNetAdhocctlGetAddrByName
sceNetAdhocctlGetNameByAddr sceNetAdhocctlGetParameter sceNetAdhocctlGetScanInfo
sceNetAdhocctlCreateEnterGameMode sceNetAdhocctlJoinEnterGameMode sceNetAdhocctlExitGameMode
sceNetAdhocctlGetGameModeInfo sceNetAdhocctlGetPeerInfo sceNetAdhocctlCreateEnterGameModeMin
sceNetAdhocInit sceNetAdhocTerm sceNetAdhocPdpCreate sceNetAdhocPdpDelete sceNetAdhocPdpSend
sceNetAdhocPdpRecv sceNetAdhocPtpOpen sceNetAdhocPtpConnect sceNetAdhocPtpListen sceNetAdhocPtpAccept
sceNetAdhocPtpSend sceNetAdhocPtpRecv sceNetAdhocPtpFlush sceNetAdhocPtpClose sceNetAdhocGetPdpStat
sceNetAdhocGetPtpStat sceNetAdhocGameModeCreateMaster sceNetAdhocGameModeCreateReplica
sceNetAdhocGameModeUpdateMaster sceNetAdhocGameModeUpdateReplica sceNetAdhocGameModeDeleteMaster
sceNetAdhocGameModeDeleteReplica sceNetAdhocSetSocketAlert sceNetAdhocGetSocketAlert sceNetAdhocPollSocket
sceNetAdhocMatchingInit sceNetAdhocMatchingTerm sceNetAdhocMatchingCreate sceNetAdhocMatchingDelete
sceNetAdhocMatchingStart sceNetAdhocMatchingStop sceNetAdhocMatchingSelectTarget
sceNetAdhocMatchingCancelTarget sceNetAdhocMatchingCancelTargetWithOpt sceNetAdhocMatchingSendData
sceNetAdhocMatchingAbortSendData sceNetAdhocMatchingSetHelloOpt sceNetAdhocMatchingGetHelloOpt
sceNetAdhocMatchingGetMembers sceNetAdhocMatchingGetPoolMaxAlloc sceNetAdhocMatchingGetPoolStat
""".split()

SOURCE_PATH_RE = re.compile(r"^(?:[A-Za-z]:)?[/\\]?[\w./\\-]*?([\w-]+\.(?:cpp|c|h))\s*\(Unknown function\)$")
CLASS_METHOD_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*::~?[A-Za-z_][A-Za-z0-9_]*)\s*\(")
NET_FUNC_RE = re.compile(r"^(Net[A-Z][A-Za-z]+|DvdUmd[A-Z][A-Za-z]+)$")
IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


def nid_of(name: str) -> int:
    return struct.unpack("<I", hashlib.sha1(name.encode("ascii")).digest()[:4])[0]


# NIDs whose real names do not hash (names assigned by the homebrew/emulator community).
NID_ALIASES = {
    0x8F2DF740: "sceKernelStopUnloadSelfModuleWithStatus",
}

NID_NAMES: dict[int, str] = {nid_of(n): n for n in KNOWN_IMPORT_NAMES}
NID_NAMES.update(MODULE_EXPORT_NIDS)
NID_NAMES.update(NID_ALIASES)


def sext16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


# --------------------------------------------------------------------------------------
# Input resolution


def _read_from_iso(iso_path: Path) -> tuple[bytes, str]:
    try:
        import pycdlib  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("ISO input requires pycdlib (pip install -r requirements.txt)") from exc
    import io

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        last_error: Exception | None = None
        for name in ("BOOT.BIN", "EBOOT.BIN"):
            for key, path in (
                ("joliet_path", f"/PSP_GAME/SYSDIR/{name}"),
                ("iso_path", f"/PSP_GAME/SYSDIR/{name};1"),
            ):
                buf = io.BytesIO()
                try:
                    iso.get_file_from_iso_fp(buf, **{key: path})
                except Exception as exc:
                    last_error = exc
                    continue
                data = buf.getvalue()
                if name == "BOOT.BIN" and (not data or data[:4] != ELF_MAGIC):
                    break  # blank/absent BOOT.BIN -> try EBOOT.BIN
                return data, f"{iso_path}::{path}"
        raise RuntimeError(f"No SYSDIR/BOOT.BIN or EBOOT.BIN found in ISO ({last_error})")
    finally:
        iso.close()


def load_executable(input_path: Path) -> tuple[bytes, str]:
    if input_path.is_file() and input_path.suffix.lower() == ".iso":
        return _read_from_iso(input_path)
    if input_path.is_file():
        return input_path.read_bytes(), str(input_path)
    if input_path.is_dir():
        candidates = []
        for base in (input_path, input_path / "SYSDIR", input_path / "PSP_GAME" / "SYSDIR",
                     input_path.parent / "SYSDIR"):
            candidates += [base / "BOOT.BIN", base / "EBOOT.BIN"]
        for cand in candidates:
            if cand.is_file():
                data = cand.read_bytes()
                if data[:4] == ELF_MAGIC or cand.name == "EBOOT.BIN":
                    return data, str(cand)
    raise RuntimeError(f"Could not find BOOT.BIN/EBOOT.BIN under {input_path}")


# --------------------------------------------------------------------------------------
# ELF / PRX model


class Prx:
    def __init__(self, blob: bytes):
        if blob[:4] == PSP_ENCRYPTED_MAGIC:
            raise ValueError(
                "Input is an encrypted ~PSP container (EBOOT.BIN). Use PSP_GAME/SYSDIR/BOOT.BIN, "
                "which is the unencrypted ELF on the Battlezone UMD."
            )
        if blob[:4] != ELF_MAGIC:
            raise ValueError("Input is not an ELF file")
        self.blob = blob
        (_, self.e_type, self.e_machine, _, self.entry, phoff, shoff, self.e_flags, _, phentsize, phnum,
         shentsize, shnum, shstrndx) = struct.unpack_from("<16sHHIIIIIHHHHHH", blob, 0)

        self.segments = []
        for i in range(phnum):
            p = struct.unpack_from("<8I", blob, phoff + i * phentsize)
            self.segments.append({"type": p[0], "offset": p[1], "vaddr": p[2], "paddr": p[3],
                                  "filesz": p[4], "memsz": p[5], "flags": p[6], "align": p[7]})

        raw = [struct.unpack_from("<10I", blob, shoff + i * shentsize) for i in range(shnum)] if shoff else []
        strtab_off = raw[shstrndx][4] if raw else 0
        self.sections = []
        for s in raw:
            name = self._cstr_at_file(strtab_off + s[0]) if raw else ""
            self.sections.append({"name": name, "type": s[1], "flags": s[2], "addr": s[3], "offset": s[4],
                                  "size": s[5], "link": s[6], "info": s[7]})
        self.by_name = {s["name"]: s for s in self.sections if s["name"]}

        text = self.by_name.get(".text")
        if text is None:
            raise ValueError("No .text section (section headers stripped?)")
        self.text_start = text["addr"]
        stub_ends = [s["addr"] + s["size"] for s in self.sections if s["name"].startswith(".sceStub.text")]
        self.code_end = max(stub_ends) if stub_ends else text["addr"] + text["size"]
        self.text_end = text["addr"] + text["size"]

    # address helpers ------------------------------------------------------------------
    def _cstr_at_file(self, off: int) -> str:
        end = self.blob.find(b"\0", off)
        return self.blob[off:end].decode("ascii", errors="replace")

    def vaddr_to_off(self, va: int) -> int | None:
        for seg in self.segments:
            if seg["type"] == 1 and seg["vaddr"] <= va < seg["vaddr"] + seg["filesz"]:
                return seg["offset"] + va - seg["vaddr"]
        return None

    def u32(self, va: int) -> int | None:
        off = self.vaddr_to_off(va)
        if off is None or off + 4 > len(self.blob):
            return None
        return struct.unpack_from("<I", self.blob, off)[0]

    def cstr(self, va: int, limit: int = 256) -> str | None:
        off = self.vaddr_to_off(va)
        if off is None:
            return None
        end = self.blob.find(b"\0", off, off + limit)
        if end < 0:
            return None
        return self.blob[off:end].decode("latin-1")

    def section_of(self, va: int) -> str:
        for s in self.sections:
            if s["addr"] and s["addr"] <= va < s["addr"] + max(s["size"], 1) and s["type"] in (1, 8):
                return s["name"]
        return ""

    def in_code(self, va: int) -> bool:
        return self.text_start <= va < self.code_end

    # module info ----------------------------------------------------------------------
    def module_info(self) -> dict[str, Any]:
        sec = self.by_name.get(".rodata.sceModuleInfo")
        if sec:
            off = sec["offset"]
        else:
            off = self.segments[0]["paddr"] & 0x7FFFFFFF
        attr, ver_lo, ver_hi = struct.unpack_from("<HBB", self.blob, off)
        name = self.blob[off + 4: off + 32].split(b"\0")[0].decode("ascii", errors="replace")
        gp, ent_top, ent_end, stub_top, stub_end = struct.unpack_from("<5I", self.blob, off + 32)
        return {"name": name, "attr": attr, "version": f"{ver_hi}.{ver_lo}", "gp": gp, "ent_top": ent_top,
                "ent_end": ent_end, "stub_top": stub_top, "stub_end": stub_end}

    def imports(self, mi: dict[str, Any]) -> list[dict[str, Any]]:
        libs = []
        va = mi["stub_top"]
        while va < mi["stub_end"]:
            name_ptr = self.u32(va)
            off = self.vaddr_to_off(va)
            if name_ptr is None or off is None:
                break
            ver, attr, size, vcount, fcount, nid_ptr, stub_ptr = struct.unpack_from("<HHBBHII", self.blob, off + 4)
            name = self.cstr(name_ptr) or f"lib_{va:08x}"
            funcs = []
            for i in range(fcount):
                nid = self.u32(nid_ptr + 4 * i) or 0
                funcs.append({"nid": nid, "name": NID_NAMES.get(nid, f"{name}_{nid:08X}"),
                              "resolved": nid in NID_NAMES, "stub": stub_ptr + 8 * i})
            libs.append({"library": name, "version": ver, "attr": attr, "functions": funcs})
            va += max(size, 5) * 4
        return libs

    def exports(self, mi: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        va = mi["ent_top"]
        while va < mi["ent_end"]:
            off = self.vaddr_to_off(va)
            if off is None:
                break
            name_ptr, ver, attr, size, vcount, fcount, tbl = struct.unpack_from("<IHHBBHI", self.blob, off)
            total = vcount + fcount
            entries = []
            for i in range(total):
                nid = self.u32(tbl + 4 * i) or 0
                addr = self.u32(tbl + 4 * (total + i)) or 0
                entries.append({"nid": nid, "name": NID_NAMES.get(nid, f"nid_{nid:08X}"), "addr": addr,
                                "kind": "function" if i < fcount else "variable"})
            out.append({"library": (self.cstr(name_ptr) if name_ptr else None) or "(module)", "entries": entries})
            va += max(size, 4) * 4
        return out

    # relocations ------------------------------------------------------------------------
    def relocations(self) -> list[tuple[int, int, int]]:
        """Return (site_vaddr, type, target_base_vaddr) sorted by site."""
        seg_base = [s["vaddr"] for s in self.segments]
        out = []
        for s in self.sections:
            if s["type"] != SHT_PSP_REL:
                continue
            for i in range(s["size"] // 8):
                r_off, info = struct.unpack_from("<II", self.blob, s["offset"] + 8 * i)
                rtype, ofs_base, addr_base = info & 0xFF, (info >> 8) & 0xFF, (info >> 16) & 0xFF
                if ofs_base >= len(seg_base) or addr_base >= len(seg_base):
                    continue
                out.append((seg_base[ofs_base] + r_off, rtype, seg_base[addr_base]))
        out.sort()
        return out


# --------------------------------------------------------------------------------------
# Analysis


class Analysis:
    def __init__(self, prx: Prx):
        self.prx = prx
        self.mi = prx.module_info()
        self.libs = prx.imports(self.mi)
        self.exps = prx.exports(self.mi)
        self.stub_names: dict[int, str] = {}
        for lib in self.libs:
            for f in lib["functions"]:
                self.stub_names[f["stub"]] = f["name"]

        self.code_refs: dict[int, int] = {}        # instruction va -> resolved data/code address
        self.calls: dict[int, int] = {}            # jal/j site -> target
        self.data_ptrs: dict[int, int] = {}        # data word va -> pointer target
        self._resolve_relocations()

        self.strings = self._collect_strings()
        self.func_starts = self._discover_functions()
        self._build_function_table()
        self._attribute_sources()
        self._auto_name()
        self.vtables = self._find_pointer_tables()

    # ----------------------------------------------------------------------------------
    def _resolve_relocations(self) -> None:
        prx = self.prx
        rels = prx.relocations()
        hi_sites: dict[int, list[tuple[int, int, int]]] = defaultdict(list)  # reg -> [(site, imm, base)]
        for site, rtype, base in rels:
            if rtype == R_MIPS_HI16:
                ins = prx.u32(site)
                if ins is not None and (ins >> 26) == 0x0F:
                    hi_sites[(ins >> 16) & 31].append((site, ins & 0xFFFF, base))
        for lst in hi_sites.values():
            lst.sort()
        hi_keys = {reg: [s for s, _, _ in lst] for reg, lst in hi_sites.items()}

        for site, rtype, base in rels:
            ins = prx.u32(site)
            if ins is None:
                continue
            if rtype == R_MIPS_26:
                target = base + ((ins & 0x03FFFFFF) << 2)
                self.calls[site] = target
            elif rtype == R_MIPS_32:
                self.data_ptrs[site] = (base + ins) & 0xFFFFFFFF
            elif rtype == R_MIPS_LO16:
                rs = (ins >> 21) & 31
                keys = hi_keys.get(rs)
                if not keys:
                    continue
                idx = bisect.bisect_right(keys, site) - 1
                if idx < 0 or site - keys[idx] > 0x400:
                    continue
                _, hi_imm, hi_base = hi_sites[rs][idx]
                target = ((hi_imm << 16) + sext16(ins & 0xFFFF)) & 0xFFFFFFFF
                self.code_refs[site] = (target + hi_base) & 0xFFFFFFFF
                self.code_refs.setdefault(hi_sites[rs][idx][0], self.code_refs[site])

    def _collect_strings(self) -> dict[int, str]:
        out: dict[int, str] = {}
        pat = re.compile(rb"[\x20-\x7e\t\r\n]{4,}\x00")
        for name in (".rodata", ".data"):
            sec = self.prx.by_name.get(name)
            if not sec:
                continue
            blob = self.prx.blob[sec["offset"]: sec["offset"] + sec["size"]]
            for m in pat.finditer(blob):
                out[sec["addr"] + m.start()] = m.group()[:-1].decode("ascii")
        return out

    def _discover_functions(self) -> list[int]:
        prx = self.prx
        starts = {prx.entry}
        starts.update(self.stub_names)
        for exp in self.exps:
            for e in exp["entries"]:
                if e["kind"] == "function" and prx.in_code(e["addr"]):
                    starts.add(e["addr"])
        for site, target in self.calls.items():
            ins = prx.u32(site) or 0
            op = ins >> 26
            if op == 3 and prx.in_code(target):  # jal
                starts.add(target)
            elif op == 2 and prx.in_code(target):  # j: only accept tail calls to function-like starts
                prev = prx.u32(target - 8)
                if prev == 0x03E00008:
                    starts.add(target)
        for target in list(self.code_refs.values()) + list(self.data_ptrs.values()):
            if prx.in_code(target) and target % 4 == 0:
                starts.add(target)
        # a function also begins right after "jr ra; <delay>" when followed by a stack frame setup
        for va in range(prx.text_start, prx.text_end - 8, 4):
            if prx.u32(va) == 0x03E00008:
                nxt = va + 8
                ins = prx.u32(nxt) or 0
                if (ins >> 16) == 0x27BD and (ins & 0x8000):  # addiu sp, sp, -N
                    starts.add(nxt)
        return sorted(s for s in starts if prx.in_code(s))

    def _build_function_table(self) -> None:
        prx = self.prx
        self.funcs: dict[int, dict[str, Any]] = {}
        starts = self.func_starts
        for i, st in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else prx.code_end
            self.funcs[st] = {"start": st, "end": end, "name": None, "source": None, "source_confidence": None,
                              "strings": [], "data_refs": [], "calls": [], "callers": [], "imports": []}
        self._func_keys = starts

        def owner(va: int) -> int | None:
            idx = bisect.bisect_right(starts, va) - 1
            return starts[idx] if idx >= 0 else None

        self.owner = owner
        for st, name in self.stub_names.items():
            if st in self.funcs:
                self.funcs[st]["name"] = name
                self.funcs[st]["source"] = "(import stub)"

        self.string_xrefs: dict[int, list[int]] = defaultdict(list)
        for site, target in sorted(self.code_refs.items()):
            f = owner(site)
            if f is None:
                continue
            ins = prx.u32(site) or 0
            if (ins >> 26) == 0x0F:  # lui half of a pair, recorded for listing only
                continue
            s_addr = self._string_at(target)
            if s_addr is not None:
                if target not in self.funcs[f]["strings"]:
                    self.funcs[f]["strings"].append(target)
                self.string_xrefs[target].append(site)
            elif target not in self.funcs[f]["data_refs"]:
                self.funcs[f]["data_refs"].append(target)

        for site, target in sorted(self.calls.items()):
            f = owner(site)
            if f is None or target not in self.funcs:
                continue
            if target in self.stub_names:
                if self.stub_names[target] not in self.funcs[f]["imports"]:
                    self.funcs[f]["imports"].append(self.stub_names[target])
            if target not in self.funcs[f]["calls"]:
                self.funcs[f]["calls"].append(target)
            if f not in self.funcs[target]["callers"]:
                self.funcs[target]["callers"].append(f)

        for va, target in self.data_ptrs.items():
            if target in self.strings:
                self.string_xrefs[target].append(va)

    def _string_at(self, va: int) -> int | None:
        return va if va in self.strings else None

    def _attribute_sources(self) -> None:
        # Direct evidence: function references an assert string naming its source file.
        direct: dict[int, str] = {}
        for st, fn in self.funcs.items():
            files = []
            for s in fn["strings"]:
                m = SOURCE_PATH_RE.match(self.strings[s].strip())
                if m:
                    files.append(m.group(1))
            game_files = [f for f in files if not f.endswith(".h")]
            chosen = game_files or files
            if chosen and fn["source"] is None:
                direct[st] = max(set(chosen), key=chosen.count)
        for st, f in direct.items():
            self.funcs[st]["source"] = f
            self.funcs[st]["source_confidence"] = "assert-string"
        # Object files are linked contiguously: fill gaps bounded by the same file.
        keys = self._func_keys
        anchored = [(i, direct[k]) for i, k in enumerate(keys) if k in direct]
        for (i0, f0), (i1, f1) in zip(anchored, anchored[1:]):
            if f0 == f1 and not f0.endswith(".h"):
                for j in range(i0 + 1, i1):
                    fn = self.funcs[keys[j]]
                    if fn["source"] is None:
                        fn["source"] = f0
                        fn["source_confidence"] = "link-order"

    def _auto_name(self) -> None:
        used: dict[str, int] = defaultdict(int)
        for st, fn in self.funcs.items():
            if fn["name"]:
                continue
            candidates: list[str] = []
            for s in fn["strings"]:
                text = self.strings[s]
                if NET_FUNC_RE.match(text):
                    candidates.append(text)
                candidates += CLASS_METHOD_RE.findall(text)
            uniq = sorted(set(candidates))
            if len(uniq) == 1:
                base = uniq[0]
                used[base] += 1
                fn["name"] = base if used[base] == 1 else f"{base}_{used[base]}"
                fn["name_source"] = "debug-string"
        for st, fn in self.funcs.items():
            if not fn["name"]:
                if st == self.prx.entry:
                    fn["name"] = "module_start"
                elif fn["source"] and fn["source"] != "(import stub)":
                    stem = IDENT_RE.sub("_", Path(fn["source"]).stem)
                    fn["name"] = f"{stem}__{st:06x}"
                else:
                    fn["name"] = f"sub_{st:06x}"

    def _find_pointer_tables(self) -> list[dict[str, Any]]:
        # Plain pointer arrays are 4 bytes apart; the SN C++ ABI used by this game lays
        # vtable entries out as {int16 this_adjust, int16 pad, code*} (8-byte stride).
        tables = []
        sites = sorted(va for va, t in self.data_ptrs.items() if self.prx.in_code(t))
        run: list[int] = []
        for va in sites + [None]:  # type: ignore[list-item]
            if run and (va is None or va - run[-1] not in (4, 8)):
                if len(run) >= 2:
                    tables.append({"address": run[0], "stride": run[1] - run[0],
                                   "slots": [self.data_ptrs[v] for v in run]})
                run = []
            if va is not None:
                run.append(va)
        return tables

    # ----------------------------------------------------------------------------------
    def fname(self, va: int) -> str:
        fn = self.funcs.get(va)
        return fn["name"] if fn else f"sub_{va:06x}"


# --------------------------------------------------------------------------------------
# Output


def describe_target(an: Analysis, target: int) -> str:
    if target in an.strings:
        text = an.strings[target]
        return json.dumps(text if len(text) <= 80 else text[:77] + "...")
    if target in an.funcs:
        return an.fname(target)
    sec = an.prx.section_of(target)
    word = an.prx.u32(target) if sec in (".rodata", ".data") else None
    extra = ""
    if word is not None:
        f = struct.unpack("<f", struct.pack("<I", word))[0]
        if 1e-6 < abs(f) < 1e7:
            extra = f" (=0x{word:08x}, f32 {f:g})"
        else:
            extra = f" (=0x{word:08x})"
    return f"{sec or 'addr'}:0x{target:06x}{extra}"


def write_listing(an: Analysis, path: Path) -> bool:
    try:
        import capstone  # type: ignore
    except Exception:
        return False
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 + capstone.CS_MODE_LITTLE_ENDIAN)
    md.skipdata = True
    prx = an.prx
    text_off = prx.vaddr_to_off(prx.text_start)
    code = prx.blob[text_off: text_off + (prx.code_end - prx.text_start)]
    xref_by_target: dict[int, list[int]] = defaultdict(list)
    for s, t in an.calls.items():
        xref_by_target[t].append(s)
    with path.open("w", encoding="utf-8") as fh:
        for ins in md.disasm(code, prx.text_start):
            va = ins.address
            fn = an.funcs.get(va)
            if fn:
                fh.write(f"\n; ==================== {fn['name']} @ 0x{va:06x}")
                if fn["source"]:
                    fh.write(f"  [{fn['source']}]")
                fh.write("\n")
                if fn["callers"]:
                    fh.write("; callers: " + ", ".join(an.fname(c) for c in fn["callers"][:12]))
                    if len(fn["callers"]) > 12:
                        fh.write(f" (+{len(fn['callers']) - 12})")
                    fh.write("\n")
            comment = ""
            if va in an.calls:
                comment = an.fname(an.calls[va])
            elif va in an.code_refs:
                comment = describe_target(an, an.code_refs[va])
            elif ins.mnemonic == "lui":
                imm = int(ins.op_str.split(",")[-1].strip(), 0) & 0xFFFF
                f = struct.unpack("<f", struct.pack("<I", imm << 16))[0]
                if imm and 1e-4 < abs(f) < 1e7:
                    comment = f"f32 hi {f:g}"
            fh.write(f"  {va:06x}:  {ins.bytes[::-1].hex()}  {ins.mnemonic:<8} {ins.op_str}")
            if comment:
                fh.write(f"    ; {comment}")
            fh.write("\n")
    return True


def build_report(an: Analysis, source_label: str, blob: bytes) -> dict[str, Any]:
    prx = an.prx
    funcs = []
    for st in an._func_keys:
        fn = an.funcs[st]
        funcs.append({
            "address": f"0x{st:06x}",
            "size": fn["end"] - st,
            "name": fn["name"],
            "source": fn["source"],
            "source_confidence": fn["source_confidence"],
            "imports": fn["imports"],
            "calls": [an.fname(c) for c in fn["calls"]],
            "callers": [an.fname(c) for c in fn["callers"]],
            "strings": [an.strings[s] for s in fn["strings"]],
            "data_refs": [f"0x{d:06x}" for d in fn["data_refs"]],
        })
    by_source: dict[str, list[str]] = defaultdict(list)
    for fn in funcs:
        if fn["source"] and fn["source"] != "(import stub)":
            by_source[fn["source"]].append(fn["address"])
    strings = [
        {"address": f"0x{va:06x}", "text": text,
         "referenced_by": sorted({an.fname(an.owner(x)) if prx.in_code(x) else f"data:0x{x:06x}"
                                  for x in an.string_xrefs.get(va, []) if an.owner(x) is not None or not prx.in_code(x)})}
        for va, text in sorted(an.strings.items())
    ]
    return {
        "input": source_label,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "elf": {"type": f"0x{prx.e_type:04x}", "entry": f"0x{prx.entry:06x}", "flags": f"0x{prx.e_flags:08x}",
                "segments": [{k: (f"0x{v:x}" if isinstance(v, int) else v) for k, v in s.items()} for s in prx.segments],
                "sections": [{"name": s["name"], "type": f"0x{s['type']:x}", "addr": f"0x{s['addr']:06x}",
                              "offset": f"0x{s['offset']:06x}", "size": s["size"]} for s in prx.sections if s["name"]]},
        "module_info": {k: (f"0x{v:x}" if isinstance(v, int) and k != "attr" else v) for k, v in an.mi.items()},
        "imports": [{"library": lib["library"], "functions": [
            {"nid": f"0x{f['nid']:08X}", "name": f["name"], "resolved": f["resolved"], "stub": f"0x{f['stub']:06x}"}
            for f in lib["functions"]]} for lib in an.libs],
        "exports": [{"library": e["library"], "entries": [
            {"nid": f"0x{x['nid']:08X}", "name": x["name"], "addr": f"0x{x['addr']:06x}", "kind": x["kind"]}
            for x in e["entries"]]} for e in an.exps],
        "stats": {
            "functions": len(funcs),
            "functions_named_from_strings": sum(1 for f in an.funcs.values() if f.get("name_source")),
            "functions_with_source": sum(1 for f in funcs if f["source"] and f["source"] != "(import stub)"),
            "code_refs": len(an.code_refs),
            "call_sites": len(an.calls),
            "data_pointers": len(an.data_ptrs),
            "strings": len(an.strings),
            "pointer_tables": len(an.vtables),
            "imports_resolved": sum(f["resolved"] for lib in an.libs for f in lib["functions"]),
            "imports_total": sum(len(lib["functions"]) for lib in an.libs),
        },
        "source_files": {k: {"functions": len(v), "first": v[0], "last": v[-1]} for k, v in sorted(by_source.items())},
        "pointer_tables": [{"address": f"0x{t['address']:06x}", "stride": t["stride"],
                            "slots": [an.fname(x) for x in t["slots"]]}
                           for t in an.vtables],
        "functions": funcs,
        "strings": strings,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    st = report["stats"]
    mi = report["module_info"]
    lines = [
        "# Battlezone PSP code map",
        "",
        f"- Input: `{report['input']}`",
        f"- SHA-256: `{report['sha256']}`",
        f"- Module: `{mi['name']}` v{mi['version']}, gp={mi['gp']}, entry={report['elf']['entry']}",
        f"- Functions: {st['functions']} ({st['functions_with_source']} attributed to a source file, "
        f"{st['functions_named_from_strings']} named from debug strings)",
        f"- Relocation-resolved code refs: {st['code_refs']}, call sites: {st['call_sites']}, "
        f"data pointers: {st['data_pointers']}, pointer tables: {st['pointer_tables']}",
        f"- Imports: {st['imports_resolved']}/{st['imports_total']} NIDs resolved",
        "",
        "## Source files (from leaked assert paths)",
        "",
        "| File | Functions | First | Last |",
        "|---|---:|---|---|",
    ]
    for name, info in report["source_files"].items():
        lines.append(f"| `{name}` | {info['functions']} | {info['first']} | {info['last']} |")
    lines += ["", "## Imports", ""]
    for lib in report["imports"]:
        names = ", ".join(f"`{f['name']}`" for f in lib["functions"])
        lines.append(f"- **{lib['library']}**: {names}")
    lines += ["", "## Named functions", "", "| Address | Name | Source |", "|---|---|---|"]
    for fn in report["functions"]:
        if not fn["name"].startswith("sub_") and fn["source"] != "(import stub)" and "__" not in fn["name"]:
            lines.append(f"| {fn['address']} | `{fn['name']}` | {fn['source'] or ''} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_relocated_elf(prx: Prx, path: Path) -> int:
    """Apply PSP relocations for a load base of 0 and write an ET_EXEC ELF.

    Segment 0 of the PRX starts at vaddr 0, so relocations against it are no-ops at
    base 0; references into segment 1 (.cplinit/.linkonce.d/.bss) still need that
    segment's vaddr added. The result loads in stock tools (Ghidra, objdump) with
    correct cross-references, no PSP-specific loader required.
    """
    img = bytearray(prx.blob)

    def rd(va: int) -> tuple[int, int] | None:
        off = prx.vaddr_to_off(va)
        return (off, struct.unpack_from("<I", img, off)[0]) if off is not None else None

    rels = prx.relocations()
    his: dict[int, list[tuple[int, int]]] = defaultdict(list)  # reg -> [(site, base)]
    for site, rtype, base in rels:
        if rtype == R_MIPS_HI16:
            got = rd(site)
            if got and (got[1] >> 26) == 0x0F:
                his[(got[1] >> 16) & 31].append((site, base))
    his_keys = {r: [s for s, _ in v] for r, v in his.items()}
    patched_hi: dict[int, int] = {}
    count = 0
    for site, rtype, base in rels:
        if base == 0:
            continue
        got = rd(site)
        if not got:
            continue
        off, ins = got
        if rtype == R_MIPS_32:
            struct.pack_into("<I", img, off, (ins + base) & 0xFFFFFFFF)
        elif rtype == R_MIPS_26:
            tgt = (((ins & 0x03FFFFFF) << 2) + base) >> 2
            struct.pack_into("<I", img, off, (ins & 0xFC000000) | (tgt & 0x03FFFFFF))
        elif rtype == R_MIPS_LO16:
            rs = (ins >> 21) & 31
            keys = his_keys.get(rs, [])
            idx = bisect.bisect_right(keys, site) - 1
            if idx < 0:
                continue
            hi_site = keys[idx]
            hi_off, hi_ins = rd(hi_site)  # type: ignore[misc]
            orig_hi = patched_hi.get(hi_site, hi_ins & 0xFFFF)
            addr = ((orig_hi << 16) + sext16(ins & 0xFFFF) + base) & 0xFFFFFFFF
            struct.pack_into("<I", img, off, (ins & 0xFFFF0000) | (addr & 0xFFFF))
            if hi_site not in patched_hi:
                patched_hi[hi_site] = hi_ins & 0xFFFF
                new_hi = ((addr + 0x8000) >> 16) & 0xFFFF
                struct.pack_into("<I", img, hi_off, (hi_ins & 0xFFFF0000) | new_hi)
        else:
            continue
        count += 1
    struct.pack_into("<H", img, 16, 2)  # e_type = ET_EXEC
    path.write_bytes(bytes(img))
    return count


def write_ghidra_symbols(an: Analysis, path: Path, base: int) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Ghidra: Script Manager -> ImportSymbolsScript.py, select this file.\n")
        fh.write(f"# Addresses assume the PRX is loaded at base 0x{base:08x}.\n")
        for st in an._func_keys:
            name = IDENT_RE.sub("_", an.funcs[st]["name"].replace("::", "__"))
            fh.write(f"{name} {base + st:08x} f\n")
        for va, text in sorted(an.strings.items()):
            label = "s_" + IDENT_RE.sub("_", text[:40]).strip("_")
            fh.write(f"{label}_{va:06x} {base + va:08x} l\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Map the Battlezone PSP executable (BOOT.BIN) for reverse engineering.")
    parser.add_argument("--input", type=Path, required=True,
                        help="BOOT.BIN / ELF PRX, extracted disc folder (root, PSP_GAME or SYSDIR) or .iso image.")
    parser.add_argument("--out-root", type=Path, required=True, help="Output directory.")
    parser.add_argument("--listing", action="store_true",
                        help="Also write an annotated disassembly listing (needs the 'capstone' package).")
    parser.add_argument("--relocated-elf", action="store_true",
                        help="Also write BOOT_relocated.elf (relocations applied, ET_EXEC) for Ghidra/objdump.")
    parser.add_argument("--ghidra-base", type=lambda v: int(v, 0), default=0,
                        help="Load base used for Ghidra symbol addresses (default 0; PPSSPP loads at 0x08804000).")
    args = parser.parse_args()

    try:
        blob, label = load_executable(args.input)
        prx = Prx(blob)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    args.out_root.mkdir(parents=True, exist_ok=True)
    print(f"Analyzing {label} ({len(blob)} bytes)")
    an = Analysis(prx)
    report = build_report(an, label, blob)
    st = report["stats"]
    (args.out_root / "code_map.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    write_markdown(report, args.out_root / "code_map.md")
    write_ghidra_symbols(an, args.out_root / "ghidra_symbols.txt", args.ghidra_base)
    print(f"[map] functions={st['functions']} sourced={st['functions_with_source']} "
          f"named={st['functions_named_from_strings']} strings={st['strings']} "
          f"imports={st['imports_resolved']}/{st['imports_total']} tables={st['pointer_tables']}")

    if args.relocated_elf:
        n = write_relocated_elf(prx, args.out_root / "BOOT_relocated.elf")
        print(f"[elf] BOOT_relocated.elf ({n} segment-1 relocations applied, load base 0)")

    if args.listing:
        if write_listing(an, args.out_root / "listing.asm"):
            print(f"[listing] {args.out_root / 'listing.asm'}")
        else:
            print("[listing] skipped: install 'capstone' to enable disassembly listings")

    summary = {k: v for k, v in st.items()}
    summary["input"] = label
    (args.out_root / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Done. out={args.out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
