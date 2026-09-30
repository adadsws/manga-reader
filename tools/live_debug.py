# 两个 CMD 共用实时监视器：显示结构化事件；TTS/模型失败附完整原因，关闭监视器不停止服务。
import argparse
import json
import os
import re
import subprocess
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {
    'tts_coverage_model_loading':'加载语音完整性检查', 'tts_coverage_model_ready':'语音完整性检查就绪',
    'tts_coverage_on':'本朗读单元启用ASR检查', 'tts_coverage_off':'本朗读单元跳过ASR检查',
    'tts_coverage_checked':'语音覆盖检查完成', 'tts_recovery_start':'开始补救漏读',
    'tts_recovery_complete':'漏读补救完成', 'tts_recovery_failed':'漏读补救未通过',
    'tts_recovery_partial':'已保留通过片段，未确认片段使用完整WAV继续朗读（待复核）',
    'tts_coverage_unverified':'ASR仍无法确认，使用完整WAV继续朗读（待复核）',
    'warmup_start':'开始启动预热', 'warmup_ocr_start':'正在预热OCR与版面模型',
    'warmup_ocr_ready':'OCR与版面模型预热完成', 'warmup_tts_start':'正在预热语音模型',
    'warmup_tts_ready':'语音模型预热完成', 'warmup_asr_start':'正在预热ASR检查',
    'warmup_asr_ready':'ASR检查预热完成', 'warmup_ready':'启动预热全部完成',
    'warmup_error':'启动预热失败',
    'request_start':'收到请求', 'request_end':'请求完成', 'request_error':'请求处理异常',
    'android_version_missing':'版本警告：连接的安卓未上报版本，可能是旧版；仍允许继续使用',
    'android_version_mismatch':'版本警告：安卓与电脑版本不同；仍允许继续使用',
    'ocr_queued':'截图已接收，等待识别', 'ocr_model_loading':'首次加载识别模型',
    'ocr_model_ready':'识别模型就绪', 'ocr_start':'正在识别与排序', 'ocr_end':'识别完成',
    'ocr_error':'识别失败', 'tts_start':'开始生成语音', 'tts_chunk_start':'正在合成语音分段',
    'tts_chunk_end':'语音分段完成', 'tts_error':'语音合成失败或已取消',
    'model_load_start':'开始加载朗读模型', 'model_load_ready':'朗读模型加载完成',
    'model_load_error':'朗读模型加载失败',
    'audio_queued':'等待语音处理', 'audio_ready':'整页音频已返回', 'page_cancelled':'页面已取消/释放',
    'overlay_ready':'朗读浮窗已启动', 'overlay_closed':'朗读浮窗已关闭',
    'capture_start':'正在截屏', 'upload_start':'正在上传截图', 'ocr_received':'已收到识别结果',
    'audio_download':'正在接收语音', 'audio_prepared':'音频准备完成', 'play_start':'开始播放',
    'sentence_gap_start':'句间停顿开始', 'sentence_gap_end':'句间停顿结束',
    'play_complete':'本页播放完成', 'turn_start':'开始翻页', 'turn_complete':'翻页完成',
    'page_unchanged':'翻页后画面未变化，已停止自动翻页',
    'auto_on':'自动翻页已开启', 'auto_off':'自动翻页已关闭', 'pause':'已暂停', 'resume':'继续播放',
    'asr_on':'ASR逐单元检查已开启', 'asr_off':'ASR逐单元检查已关闭',
    'cancel':'当前任务已停止', 'error':'安卓操作失败，请查看浮窗提示',
}
FIELDS = {'bytes':'字节', 'rows':'行数', 'chunks':'总段数', 'chunk':'当前段',
          'characters':'字数', 'seconds':'耗时秒', 'status':'HTTP', 'index':'播放单元',
          'android_version':'安卓版本', 'computer_version':'电脑版本',
          'generation':'任务序号', 'auto':'自动翻页'}

def format_event(data):
    name = data.get('event')
    if not isinstance(name,str) or name not in EVENTS: return None
    fields = [f'{label}={data[key]}' for key,label in FIELDS.items()
              if key in data and isinstance(data[key], (int,float,bool))]
    if name in ('tts_error','model_load_error','warmup_error') and isinstance(data.get('reason'),str):
        fields.append('完整原因='+data['reason'])
    return EVENTS[name] + (' | ' + ' '.join(fields) if fields else '')

class JsonTail:
    def __init__(self, path, history=True):
        self.path=Path(path); self.identity=None; self.offset=0; self.pending=b''
        if not history:
            try:
                stat=self.path.stat(); self.identity=(stat.st_dev,stat.st_ino); self.offset=stat.st_size
            except FileNotFoundError: pass
    def read(self):
        try:
            with self.path.open('rb') as stream:
                stat=os.fstat(stream.fileno()); identity=(stat.st_dev,stat.st_ino)
                if identity!=self.identity or stat.st_size<self.offset:
                    self.identity=identity; self.offset=max(0,stat.st_size-65536); self.pending=b''
                    if self.offset: stream.seek(self.offset); stream.readline(); self.offset=stream.tell()
                stream.seek(self.offset); raw=stream.read(1_000_000); self.offset=stream.tell()
        except (FileNotFoundError,PermissionError): return []
        lines=(self.pending+raw).split(b'\n'); self.pending=lines.pop()
        if len(self.pending)>65536: self.pending=b''
        output=[]
        for line in lines:
            try:
                data=json.loads(line)
                if isinstance(data,dict) and (text:=format_event(data)): output.append(text)
            except (ValueError,UnicodeDecodeError): pass
        return output

def android_event(line):
    # 不接受 Reader 正文日志、任意错误字符串或第三方输出。
    m=re.fullmatch(r'event=([a-z_]+) generation=(\d+) auto=(true|false)',line.strip())
    if not m: return None
    return format_event({'event':m[1],'generation':int(m[2]),'auto':m[3]=='true'})


def emit(source, text):
    print(f'[{datetime.now():%H:%M:%S}] [{source}] {text}',flush=True)

class ChildJob:
    """Windows 关闭 CMD/监视器时自动清理其 logcat，绝不包含服务或模拟器。"""
    def __init__(self, process):
        self.handle=None
        if os.name!='nt': return
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                      ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];api.CreateJobObjectW.restype=w.HANDLE
        api.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        api.CloseHandle.argtypes=[w.HANDLE]
        self.api=api;self.handle=api.CreateJobObjectW(None,None)
        limits=Limits();limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(self.handle,int(process._handle)):
            error=ctypes.get_last_error();self.close();raise OSError(error,'无法设置日志子进程清理边界')
    def close(self):
        if self.handle: self.api.CloseHandle(self.handle);self.handle=None

class AndroidStream:
    def __init__(self, adb):
        self.adb=adb;self.process=None;self.job=None;self.lines=queue.Queue()
        self.cursor=None;self.boundary=set()
    def connect(self):
        self.close()
        args=[str(self.adb),'-s','emulator-5554']
        avd=subprocess.run(args+['emu','avd','name'],capture_output=True,timeout=3)
        if not avd.stdout.splitlines() or avd.stdout.splitlines()[0].strip()!=b'ReaderAosp35':
            raise OSError('专用模拟器未连接')
        if self.cursor is None:
            stamp=subprocess.check_output(args+['shell','date','+%s.%N'],timeout=3).decode().strip()
            if not re.fullmatch(r'\d+\.\d+',stamp): raise OSError('设备时间不可读取')
            self.cursor=stamp
        self.lines=queue.Queue()
        self.process=subprocess.Popen(args+['logcat','-v','epoch','-v','usec','-T',self.cursor,'-s','ReaderDebug:I','*:S'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',bufsize=1)
        try: self.job=ChildJob(self.process)
        except Exception: self.close();raise
        child=self.process; output=self.lines
        def receive():
            for line in child.stdout: output.put(line.rstrip())
            output.put(None)
        threading.Thread(target=receive,daemon=True).start()
    def parse(self,line):
        stamp=line.split(maxsplit=1)[0] if line.strip() else ''
        if not re.fullmatch(r'\d+\.\d+',stamp): return None
        if self.cursor is not None and float(stamp)<float(self.cursor): return None
        if stamp!=self.cursor: self.cursor=stamp;self.boundary.clear()
        if line in self.boundary: return None
        self.boundary.add(line)
        payload=line.partition('ReaderDebug: ')[2] or line.partition('ReaderDebug : ')[2]
        return android_event(payload)
    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired: self.process.kill();self.process.wait()
            if self.process.stdout: self.process.stdout.close()
            self.process=None
        if self.job is not None: self.job.close();self.job=None

def watch(source, duration=0):
    started=time.monotonic();retry=0;state=None
    tail=JsonTail(ROOT/'~temp/logs/debug.jsonl',history=False)
    stream=AndroidStream(ROOT/'~temp/android-sdk/platform-tools/adb.exe')
    emit(source,'事件监视已开启；有新事件立即追加，空闲时不输出。关闭窗口只退出监视。')
    try:
        while not duration or time.monotonic()-started<duration:
            if source=='电脑':
                for text in tail.read(): emit(source,text)
                time.sleep(.05)
            else:
                if stream.process is None:
                    if time.monotonic()<retry: time.sleep(.1);continue
                    try:
                        stream.connect()
                        if state!='connected': emit(source,'已连接安卓事件流')
                        state='connected'
                    except (OSError,subprocess.TimeoutExpired):
                        if state!='disconnected': emit(source,'安卓事件流已断开，等待重连')
                        state='disconnected';retry=time.monotonic()+3;continue
                try: line=stream.lines.get(timeout=.1)
                except queue.Empty: continue
                if line is None:
                    stream.close()
                    if state!='disconnected': emit(source,'安卓事件流已断开，等待重连')
                    state='disconnected';retry=time.monotonic()+3
                elif (text:=stream.parse(line)): emit(source,text)
    except KeyboardInterrupt: emit(source,'已退出事件监视；服务和模拟器继续运行')
    finally: stream.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',choices=['pc','android']);parser.add_argument('--duration',type=float,default=0)
    args=parser.parse_args();watch('电脑' if args.source=='pc' else '安卓',args.duration)
