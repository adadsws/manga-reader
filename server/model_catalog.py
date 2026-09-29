# 主机模型目录：动态扫描非归档 GPT-SoVITS 资源，生成安全选择 ID 并管理运行状态。
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ARCHIVE_NAMES={"archive","~archive"}
IMAGE_EXTENSIONS={".png",".jpg",".jpeg",".webp"}
AUDIO_EXTENSIONS={".wav",".mp3",".flac",".ogg",".m4a"}
VERSION_RE=re.compile(r"v\d+(?:(?:[._-]?pro)(?:[._-]?plus)?)?",re.I)
GENERIC_MODEL_DIRECTORIES={"model","models","weight","weights","checkpoint","checkpoints","output","outputs"}


def _epoch(name):
    match=re.search(r"(?:^|[-_])e(\d+)(?:[-_.]|$)",name,re.I)
    return int(match.group(1)) if match else -1


def _prompt(path):
    text=path.stem
    text=re.sub(r"^【默认】", "", text)
    return text.replace("_", "").strip()


def _inside(path,parent):
    try:path.resolve().relative_to(parent.resolve());return True
    except ValueError:return False


def _is_version(value):
    return bool(VERSION_RE.fullmatch(value))


class ModelCatalog:
    def __init__(self,root,settings,state_path=None):
        self.root=Path(root).resolve()
        self.settings=settings
        self.default_reference=Path(settings.get("reference_audio",""))
        self.default_prompt_lang=settings.get("prompt_lang","zh")
        self.state_path=Path(state_path) if state_path else self.root.parents[1]/"~temp/model-selection.json"
        self.validation_path=self.state_path.with_name("model-validation.json")
        self.lock=threading.RLock()
        self.active_id=None
        self.loaded_id=None
        self.validation=self._read(self.validation_path,{})
        saved=self._read(self.state_path,{})
        self.requested_id=saved.get("model_id")
        self.last_scan={}
        self._apply_saved_metadata(saved)

    @staticmethod
    def _read(path,fallback):
        try:return json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError,TypeError):return fallback

    @staticmethod
    def _write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+".tmp")
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        temporary.replace(path)

    def _apply_saved_metadata(self,saved):
        # 只采用没有文件路径的安全元数据；真实路径每次重新扫描。
        for key in ("voice","voice_name","model_version","reference_text","prompt_lang"):
            if isinstance(saved.get(key),str):self.settings[key]=saved[key]

    def _excluded(self,path):
        return any(part.casefold() in ARCHIVE_NAMES for part in path.relative_to(self.root).parts)

    def _local_image(self,directory):
        files=[p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
        preferred=next((p for p in files if p.stem in ("角色头像","头像")),None)
        return preferred or (sorted(files,key=lambda p:p.name.casefold())[0] if files else None)

    def _local_audio(self,directory):
        files=[p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
        return next((p for p in sorted(files,key=lambda p:p.name.casefold()) if p.name.startswith("【默认】")),None) or (sorted(files,key=lambda p:p.name.casefold())[0] if files else None)

    def _role_root(self,directory):
        # 同时兼容“角色/版本”和“版本/任意分组/角色”；只在能确定角色边界时继承资源。
        if _is_version(directory.name) and directory.parent!=self.root:return directory.parent
        if directory.name.casefold() in GENERIC_MODEL_DIRECTORIES and directory.parent!=self.root:return directory.parent
        return directory

    def _nearest_avatar(self,directory):
        avatar=self._local_image(directory)
        if avatar:return avatar
        role_root=self._role_root(directory)
        if role_root!=directory:return self._local_image(role_root)
        return None

    def _reference(self,directory):
        local=self._local_audio(directory)
        if local:return local
        # 型号子目录可从同一角色根目录的数据集取参考音频，绝不跨到其他角色。
        voice_root=self._role_root(directory)
        if voice_root!=directory:
            if self.default_reference.is_file() and _inside(self.default_reference,voice_root):return self.default_reference
            nested=sorted((p for p in voice_root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and not self._excluded(p.parent)),key=lambda p:(not p.name.startswith("【默认】"),p.relative_to(voice_root).as_posix().casefold()))
            if nested:return nested[0]
        return None
    def _language(self,path,reference):
        joined="/".join(path.relative_to(self.root).parts)
        if "日语" in joined or "日本語" in joined:return "ja"
        if reference and self.default_reference.resolve()==reference.resolve():return self.default_prompt_lang
        return "zh"

    def _version(self,directory):
        for part in reversed(directory.relative_to(self.root).parts):
            if _is_version(part):return part
        return directory.parent.name if directory.parent!=self.root else directory.name

    def _voice_name(self,directory):
        if _is_version(directory.name) and directory.parent!=self.root:return directory.parent.name
        if directory.name.casefold() in GENERIC_MODEL_DIRECTORIES and directory.parent!=self.root:return directory.parent.name
        return directory.name

    def scan(self):
        with self.lock:
            models=[]
            if not self.root.is_dir():return {"models":[],"tree":[],"counts":{"total":0,"available":0,"unverified":0,"incomplete":0,"failed":0},"active_id":None,"error":"模型根目录不存在"}
            validation_changed=False
            state_changed=False
            for directory in sorted((p for p in self.root.rglob("*") if p.is_dir()),key=lambda p:p.relative_to(self.root).as_posix().casefold()):
                if self._excluded(directory):continue
                files=[p for p in directory.iterdir() if p.is_file()]
                gpts=sorted((p for p in files if p.suffix.lower()==".ckpt"),key=lambda p:(_epoch(p.name),p.name.casefold()))
                sovits=sorted((p for p in files if p.suffix.lower()==".pth"),key=lambda p:(_epoch(p.name),p.name.casefold()))
                if not gpts and not sovits:continue
                reference=self._reference(directory);avatar=self._nearest_avatar(directory)
                choices=sovits or [None]
                for sovits_path in choices:
                    gpt_path=min(gpts,key=lambda p:(abs(_epoch(p.name)-_epoch(sovits_path.name)), -_epoch(p.name),p.name.casefold())) if gpts and sovits_path else (gpts[-1] if gpts else None)
                    rel=directory.relative_to(self.root).as_posix()
                    voice_name=self._voice_name(directory);version=self._version(directory)
                    signature="|".join((voice_name.casefold(),version.casefold(),gpt_path.name if gpt_path else "",str(gpt_path.stat().st_size) if gpt_path else "",sovits_path.name if sovits_path else "",str(sovits_path.stat().st_size) if sovits_path else ""))
                    model_id=hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
                    reference_names={reference.name,re.sub(r"^【默认】","",reference.name)} if reference else {""}
                    compat_ids={hashlib.sha256("|".join((signature,name)).encode("utf-8")).hexdigest()[:20] for name in reference_names}
                    if reference:
                        try:legacy_reference=reference.relative_to(self.root).as_posix()
                        except ValueError:legacy_reference=reference.name
                    else:legacy_reference=""
                    legacy_signature="|".join((rel,gpt_path.name if gpt_path else "",sovits_path.name if sovits_path else "",legacy_reference))
                    legacy_id=hashlib.sha256(legacy_signature.encode("utf-8")).hexdigest()[:20]
                    errors=[]
                    if not gpt_path:errors.append("缺少 GPT 权重（.ckpt）")
                    if not sovits_path:errors.append("缺少 SoVITS 权重（.pth）")
                    if not reference:errors.append("缺少参考音频（.wav）")
                    prompt=_prompt(reference) if reference else ""
                    if not prompt:errors.append("无法从参考音频文件名取得提示文字")
                    if not avatar:errors.append("缺少头像（png/jpg/jpeg/webp）")
                    validation=self.validation.get(model_id,{})
                    if not validation:
                        validation=next((self.validation[item] for item in compat_ids if item in self.validation),self.validation.get(legacy_id,{}))
                    if validation and model_id not in self.validation:
                        self.validation[model_id]=validation;validation_changed=True
                    if self.requested_id==legacy_id or self.requested_id in compat_ids:
                        self.requested_id=model_id;state_changed=True
                    file_errors=list(errors)
                    if errors:state="incomplete"
                    elif validation.get("status")=="failed":state="failed"
                    elif validation.get("status")=="available":state="available"
                    else:state="unverified"
                    runtime_error=validation.get("error") if state=="failed" else None
                    if runtime_error:errors.append(runtime_error)
                    checkpoint=sovits_path.stem if sovits_path else (gpt_path.stem if gpt_path else "无权重")
                    models.append({"id":model_id,"path":list(directory.relative_to(self.root).parts),"relative_path":rel,"name":voice_name,"version":version,"checkpoint":checkpoint,"gpt":gpt_path,"sovits":sovits_path,"reference":reference,"reference_text":prompt,"prompt_lang":self._language(directory,reference),"avatar":avatar,"state":state,"selectable":not bool(file_errors),"errors":errors,"validated_at":validation.get("validated_at")})
            if validation_changed:self._write(self.validation_path,self.validation)
            if state_changed:
                saved=self._read(self.state_path,{})
                saved["model_id"]=self.requested_id
                self._write(self.state_path,saved)
            by_id={item["id"]:item for item in models}
            if self.requested_id in by_id and by_id[self.requested_id]["selectable"]:
                self.active_id=self.requested_id
                self._apply_model_settings(by_id[self.active_id])
            elif self.active_id not in by_id:
                self.active_id=self._default_id(models)
                if self.requested_id is None and self.loaded_id is None:self.loaded_id=self.active_id
            self.last_scan=by_id
            public=[self._public(item) for item in models]
            counts={name:sum(x["state"]==name for x in public) for name in ("available","unverified","incomplete","failed")}
            counts["total"]=len(public)
            return {"models":public,"tree":self._tree(public),"counts":counts,"active_id":self.active_id,"scanned_at":int(time.time())}

    def _default_id(self,models):
        voice=self.settings.get("voice_name","").casefold();version=self.settings.get("model_version","").casefold()
        matches=[m for m in models if m["selectable"] and m["name"].casefold()==voice and m["version"].casefold()==version]
        chosen=max(matches,key=lambda m:(_epoch(m["checkpoint"]),m["checkpoint"])) if matches else next((m for m in models if m["selectable"]),None)
        if chosen:self._apply_model_settings(chosen)
        return chosen["id"] if chosen else None

    def _apply_model_settings(self,item):
        self.settings.update({"voice":item["name"],"voice_name":item["name"],"model_version":item["version"],"reference_audio":str(item["reference"]),"reference_text":item["reference_text"],"prompt_lang":item["prompt_lang"]})

    def _public(self,item):
        return {key:value for key,value in item.items() if key not in ("gpt","sovits","reference","avatar")} | {"active":item["id"]==self.active_id,"avatar_url":"/models/"+item["id"]+"/avatar" if item["avatar"] else None}

    def _tree(self,models):
        root=[]
        for item in models:
            level=root
            for depth,name in enumerate(item["path"]):
                node=next((x for x in level if x.get("kind")=="group" and x["name"]==name),None)
                if node is None:
                    node={"kind":"group","name":name,"children":[]};level.append(node)
                level=node["children"]
            level.append({"kind":"model",**item})
        return root

    def avatar(self,model_id):
        self.scan();item=self.last_scan.get(model_id)
        return item.get("avatar") if item else None

    def active_artifact_signatures(self):
        """Return cheap, stable signatures for the artifacts loaded by GPT-SoVITS.

        This is called only while building the cached TTS history context. File
        size and nanosecond mtime make an in-place checkpoint replacement miss
        old audio without hashing multi-gigabyte model files. It deliberately
        never scans: the normal warmup/model-selection path already populated
        ``last_scan``, while an uninitialised catalog must not delay a hit check.
        """
        with self.lock:
            item = self.last_scan.get(self.active_id)
            if not item:
                return []
            signatures = []
            for kind in ("gpt", "sovits", "reference"):
                path = item.get(kind)
                try:
                    stat = path.stat()
                    signature = [kind, str(path.resolve()), stat.st_size, stat.st_mtime_ns]
                except (AttributeError, OSError, TypeError):
                    signature = [kind, str(path), None, None]
                signatures.append(signature)
            return signatures

    def _load(self,item):
        for endpoint,path in (("set_gpt_weights",item["gpt"]),("set_sovits_weights",item["sovits"])):
            url=self.settings["tts_url"].rstrip("/")+"/"+endpoint+"?"+urllib.parse.urlencode({"weights_path":str(path)})
            try:
                with urllib.request.urlopen(url,timeout=240) as response:
                    body=response.read()
                    if response.status!=200:raise RuntimeError(endpoint+" HTTP "+str(response.status))
                    try:payload=json.loads(body)
                    except ValueError:payload={"raw":body.decode("utf-8",errors="replace")}
                    if isinstance(payload,dict) and payload.get("message") not in (None,"success","Success") and payload.get("code") not in (None,0):raise RuntimeError(endpoint+"："+str(payload))
            except urllib.error.HTTPError as exc:
                # GPT-SoVITS 把权重加载异常放在 400 JSON 正文中；保留正文供安卓和诊断日志显示根因。
                raw=exc.read().decode("utf-8",errors="replace")
                try:
                    error=json.loads(raw)
                    parts=[str(error.get(key)) for key in ("message","Exception","detail") if error.get(key)] if isinstance(error,dict) else []
                    detail="：".join(parts) or raw
                except ValueError:detail=raw
                raise RuntimeError(f"{endpoint} HTTP {exc.code}：{detail or exc.reason}") from exc

    def select(self,model_id,persist=True,probe=None):
        with self.lock:
            self.scan();item=self.last_scan.get(model_id)
            if not item:raise ValueError("模型不存在或已被移除")
            if not item["selectable"]:raise ValueError("模型文件不完整："+"；".join(item["errors"]))
            previous=self.last_scan.get(self.active_id)
            keys=("voice","voice_name","model_version","reference_audio","reference_text","prompt_lang")
            old_settings={key:self.settings.get(key) for key in keys}
            try:
                self._load(item)
                self._apply_model_settings(item)
                if probe is not None:probe()
            except Exception as exc:
                rollback=""
                if previous and previous["id"]!=model_id:
                    try:self._load(previous)
                    except Exception as rollback_exc:rollback="；原模型恢复失败："+str(rollback_exc)
                for key,value in old_settings.items():
                    if value is None:self.settings.pop(key,None)
                    else:self.settings[key]=value
                message="模型加载或试读失败："+str(exc)+rollback
                self.validation[model_id]={"status":"failed","error":message,"validated_at":int(time.time())};self._write(self.validation_path,self.validation)
                raise RuntimeError(message) from exc
            self.validation[model_id]={"status":"available","validated_at":int(time.time())};self._write(self.validation_path,self.validation)
            self.active_id=self.loaded_id=self.requested_id=model_id
            if persist:self._write(self.state_path,{"model_id":model_id,"voice":self.settings["voice"],"voice_name":self.settings["voice_name"],"model_version":self.settings["model_version"],"reference_text":self.settings["reference_text"],"prompt_lang":self.settings["prompt_lang"]})
            return self._public({**item,"state":"available","validated_at":self.validation[model_id]["validated_at"]})

    def ensure_loaded(self):
        self.scan()
        if self.active_id and self.loaded_id!=self.active_id:self.select(self.active_id,persist=False)
