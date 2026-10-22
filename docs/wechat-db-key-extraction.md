# 微信数据库密钥提取算法参考（Linux / Windows / macOS）

> **本文档的存在意义**：上游参考仓库有被 DMCA 或作者主动清空的先例
> （sjzar/chatlog 已删除全部代码与历史，ycccccccy/wx_key 已收 DMCA 通知）。
> 为避免"库没了算法就失传"，这里以伪代码形式沉淀三平台**当前已验证可用**的核心算法。
> 记录基线：2026-09-15，微信 Linux 4.1.13.9（x86_64）实测通过（19/19 库密钥验证）。
> 各版本细节可能随微信更新漂移，但锚点串/结构偏移类的自适应思路一般能延续。

---

## 0. 共用基础（三平台一致）

微信 4.x 的本地库为 WCDB（SQLCipher 4 封装）。每库独立 salt，密钥体系两级：

- **passphrase**（32 字节）：账号级，登录时计算，换设备重新登录会轮换
- **enc_key**（32 字节）：每库一个，由 passphrase 派生，即 `WECHAT_DB_KEY` JSON 里的值

```
db_salt  = db 文件头 16 字节（明文）
enc_key  = PBKDF2-HMAC-SHA512(password=passphrase, salt=db_salt, iter=256000, dklen=32)
mac_salt = db_salt 每字节 XOR 0x3A
mac_key  = PBKDF2-HMAC-SHA512(password=enc_key,  salt=mac_salt, iter=2,       dklen=32)

# page-1 校验（判断候选 enc_key 是否正确的唯一判据，恒定时间比较）
expected = HMAC-SHA512(mac_key, page1[16:4032] + le_u32(1))
valid    = (expected == page1[4032:4096])

常量: PAGE=4096, RESERVE=80(IV16+HMAC64), KDF_ITER=256000
页解密: AES-256-CBC, iv = page[4016:4032], 密文 = page[16:4016]（page1）/ page[0:4016]（其余页）
```

---

## 1. Linux

### 1.1 版本演进

| 微信版本 | 内存中的密钥形态 | 可用方案 |
|---|---|---|
| ≤ 4.1.12 | `x'<64hex密钥><32hex盐>'` SQL 字面量明文缓存 | 直接扫内存 |
| ≥ 4.1.13 | 明文密钥不再驻留内存，只剩 passphrase（登录时经 WCDB cipher 配置函数传入） | ptrace 断点捕获 passphrase + PBKDF2 派生 |

### 1.2 旧方案（≤4.1.12）：内存字面量扫描

```
扫描 /proc/<pid>/mem 的全部 rw 区（8MB 分块 + 尾部重叠防跨界漏配）：
    正则  x'([0-9a-fA-F]{96})'
    前 64 hex = enc_key, 后 32 hex = salt
    按 salt 对齐到本地 .db 文件头 → 用 §0 校验 → 输出
```

### 1.3 新方案（≥4.1.13）：验证式 passphrase 捕获

**步骤一：ELF 静态分析定位 cipher 配置函数**（微信更新自适应，不硬编码偏移）

```
anchor = ".rodata" 中搜索 b"com.Tencent.WCDB.Config.Cipher"
对 .text 全文做 RIP 相对解码找引用：
    f1 = 所有 `lea rsi, [rip+X]`（X 命中 anchor）的位置
    过滤: f1 前 7 字节必须是 `lea rdi, [rip+Y]`（Y 指向一个匿名配置结构）
    f2 = 所有 `lea rsi, [rip+Z]`（Z 命中该结构地址）
    从 f2 向回搜 ≤0x500 字节内的函数序言 `55 41 57`（push rbp; push r15）
hook_va = 序言地址 - 镜像基址
```

**步骤二：ptrace 捕获（纯 ctypes，无 gdb 依赖）**

```
前提: 微信已关闭（passphrase 只在登录时计算）
1. 以子进程方式启动微信; 轮询 /proc 找到新 wechat 主进程
2. PTRACE_ATTACH 主线程（exec 后立刻附加，此时单线程）+ 已有线程;
   SETOPTIONS = TRACEFORK|TRACEVFORK|TRACECLONE|TRACEEXEC（覆盖后续所有线程）
3. 基址 = maps 中该二进制 file_offset=0 映射的起始
   ⚠ 不能取 r-xp 段起始当基址（大 rodata 在前时偏移可达 70MB+，wcdb-key-tool#2）
4. bp = 基址 + hook_va; 读-改-写机器码首字节为 0xCC（INT3，按 8 字节字对齐改写）
5. 循环 waitpid(-1, __WALL):
     断点命中 → 读寄存器, 生成候选（两种调用布局）:
         a) rdx == 32      → 候选 = [rsi] 起始 32 字节
         b) *(rsi+16)==32  → 候选 = [*(rsi+8)] 起始 32 字节
     每个候选当场验证: enc=PBKDF2(候选, probe库salt) → §0 校验 probe 库 page1
     ⚠ cipher 配置函数会被多次调用，首个 32 字节参数不是 passphrase——
       验证不过就: 还原指令 → 单步执行 → 重新写 INT3 → 继续等下一次调用
     验证通过 → 还原指令 → detach → 返回 passphrase
6. teardown: 逐个 CONT+DETACH 所有被追踪线程，最后 kill(SIGCONT)
   （PTRACE_DETACH 不会唤醒 group-stop 的线程，漏了会冻死微信 UI）
```

**步骤三：派生** —— 对每库跑 §0 公式，只输出验证通过的。

本仓库实现：`tools/extract_wx_keys_linux.py --mode passphrase`。

密钥 JSON 只包含提取时已存在且校验通过的数据库。微信后续新建或重建 `.db` 时，需重新派生相应路径的 key；例如首次发送语音后可能才出现 `message/media_0.db`。已保存且仍有效的 passphrase 可按新库 salt 派生，但 Gateway/Chatlog 当前不会自动补 key；更新配置后重启读取服务。不要把 passphrase 或完整 key 写入日志。

---

## 2. Windows（Weixin.exe）

### 2.1 版本演进

| 微信版本 | 可用方案 |
|---|---|
| 4.0.x | raw key 明文驻留内存，直接扫 `x'...'` 字面量（同 §1.2，工具：ReadProcessMemory） |
| 4.1.11+ | 只读运行时 **Config.Cipher 结构扫描**（见 2.2） |
| 4.1+（研究） | bcrypt!`BCryptDeriveKeyPBKDF2` 条件断点抓 passphrase（wcdb-key-tool 标注实验性） |

### 2.2 Windows 4.1.11+：Config.Cipher 运行时扫描（只读，无需注入）

```
needle = b"com.Tencent.WCDB.Config.Cipher"

1. 枚举 Weixin.exe（注意进程名是 Weixin 不是 WeChat）全部可读区
2. 在内存中找 needle 的所有出现地址 A_i
3. 再扫一遍内存找 (ptr=A_i, len=len(needle)) 的 8+8 字节对 → std::string 对象引用 qaddr
4. node = read(qaddr - 0x10, 0x50)
   校验: *(node+0x10) ∈ A_i 且 *(node+0x18) == len(needle)
5. config = *(node + 0x28)                       # 指向 Cipher 配置对象
6. obj = read(config + 0x88, 0x28); data = *(obj+0x8), len = *(obj+0x10)   # 1 ≤ len ≤ 1024
7. blob = read(data, len)
8. decoded = blob XOR 循环掩码（32 字节定长，见下）——掩码本身来自微信代码段常量
9. decoded 内匹配 [xX]'([0-9a-fA-F]{64,192})' 字面量:
     取 64hex 窗口为 enc_key（窗口起点 0 及步长 32 的全部对齐）
     若有后缀 32hex 则为该库 salt，可定向验证；否则全库暴力验证（§0）
```

XOR 掩码（16 进制，三平台唯一硬编码常量，版本更新可能变化）：

```
d2c7442458020000004889442450488b
450048844c2448488944254048584c24
```

### 2.3 备选：CNG 断点（研究性）

```
bp bcrypt!BCryptDeriveKeyPBKDF2, 条件 rdx==32（x64 调用约定第 2/3 参）
命中时读 rsi 指向的 32 字节 → passphrase → §0 派生
（对应 Linux 的 WCDB cipher 配置断点；DLL 注入 + Hook 方案见已 DMCA 的 wx_key）
```

---

## 3. macOS

### 3.1 前置：重签名（否则内核拒绝 task_for_pid）

```
sudo codesign --force --deep --sign - /Applications/WeChat.app
# 微信每次更新后需重做；系统更新也可能还原签名
```

### 3.2 内存扫描（raw key 字面量仍存在的版本）

```
task_for_pid(WeChat)                       # libSystem ctypes
mach_vm_region 枚举可读区 → mach_vm_read 逐段读
正则  x'([0-9a-f]{96})'                    # 注意 macOS 上是小写
前 64 hex = enc_key, 后 32 hex = salt → §0 校验
```

### 3.3 lldb 断点捕获 passphrase（4.1+）

macOS 微信二进制**带符号**，可直接按符号断点（比 Linux 的 ELF 锚点链简单得多）：

```
lldb 脚本:
    process attach -p <pid>
    breakpoint set -n CCKeyDerivationPBKDF -c '$x2 == 32'   # arm64: x1=ptr, x2=len
                                                            # x86_64: rsi/rdx
    memory read --size 1 --count 32 --format x $x1          # 命中时读 32 字节
触发条件: 用户退出账号后重新登录（passphrase 在登录时派生）
从 lldb 的 hexdump 输出解析出 passphrase → §0 派生
注意: lldb 异步等待期间要用 `sleep` 保活 stdin，读到 EOF 会直接退出等不到断点
```

---

## 4. passphrase 轮换与运维要点

- 换设备登录 / 部分安全事件会**轮换 passphrase** → 全部库密钥失效，需重新捕获
- Linux 端捕获可借**自动重登**触发（无需扫码）；macOS/断点方案通常需要显式退出重登
- 密钥仅对本机当前数据目录有效；`WECHAT_DB_KEY` 泄露等同于聊天记录泄露，注意 0600 权限

---

## 5. 参考仓库清单（截至 2026-09-15 可访问）

| 平台 | 仓库 | 许可证 | 说明 |
|---|---|---|---|
| Linux | [boujuan/extract-wechat-messages-linux](https://github.com/boujuan/extract-wechat-messages-linux) | MIT | 验证式捕获 tracer + ELF 锚点链，README 注明已在 **4.1.13.9** 验证；本文 §1.3 主要参考 |
| 三平台 | [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) | MIT | Linux/macOS/Windows 全覆盖；本文 §2/§3 主要参考；其 Linux PIE 基址 bug 见 [Issue #2](https://github.com/TANGandXUE/wcdb-key-tool/issues/2) |
| 历史(Linux) | [L1en2407/wechat-decrypt](https://github.com/L1en2407/wechat-decrypt) | — | SQLCipher4 逐页解密的算法出处（wxextract 注明引用） |
| 历史(Win) | [0xlane/wechat-dump-rs](https://github.com/0xlane/wechat-dump-rs) | MIT | Windows 内存扫描路线的早期标杆 |
| ⚠ 已失效 | sjzar/chatlog | — | **已删除全部代码与提交历史**（法律原因），仅剩 README——本文档存在的直接动机 |
| ⚠ 已失效 | ycccccccy/wx_key | — | Windows DLL 注入 + Hook 方案，**已收 DMCA 通知**停止维护 |

> 法律提示：以上算法仅应用于**导出本人聊天记录**（备份/迁移/本地检索），
> 请遵守所在地法律法规及腾讯用户协议。
