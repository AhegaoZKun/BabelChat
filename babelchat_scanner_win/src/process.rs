//! Opening the game's process, reading its memory, and listing what of it can
//! be read at all. Nothing here knows what a chat buffer is.

#[cfg(windows)]
use windows::Win32::{
    System::Threading::{GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_IDLE},
    Foundation::HANDLE,
    System::{
        Diagnostics::Debug::ReadProcessMemory,
        Memory::{VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT,
                 PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, PAGE_READONLY,
                 PAGE_READWRITE, PAGE_WRITECOPY},
        Threading::{OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ},
    },
};

pub(crate) const CHUNK_SIZE: usize = 65536;
pub(crate) const MAX_BUF_READ: usize = 65536;
pub(crate) const MAX_REGION_SIZE: usize = 100 * 1024 * 1024; // 100MB
pub(crate) const MAX_ADDRESS: usize = 0x7FFF_FFFF_FFFF;

// ── Process handle ────────────────────────────────────────────────────────────

#[cfg(windows)]
pub(crate) fn open_process(pid: u32) -> Option<HANDLE> {
    unsafe {
        let h = OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
            false,
            pid,
        ).ok()?;
        if h.is_invalid() { return None; }
        Some(h)
    }
}

#[cfg(not(windows))]
pub(crate) fn open_process(_pid: u32) -> Option<()> { None }

// ── Memory reading ────────────────────────────────────────────────────────────

#[cfg(windows)]
pub(crate) fn read_memory(handle: HANDLE, addr: usize, buf: &mut [u8]) -> bool {
    let mut bytes_read = 0usize;
    unsafe {
        ReadProcessMemory(
            handle,
            addr as *const _,
            buf.as_mut_ptr() as *mut _,
            buf.len(),
            Some(&mut bytes_read),
        ).is_ok() && bytes_read > 0
    }
}

#[cfg(not(windows))]
pub(crate) fn read_memory(_handle: (), _addr: usize, _buf: &mut [u8]) -> bool { false }

// ── Region enumeration ────────────────────────────────────────────────────────

#[derive(Clone)]
pub(crate) struct Region {
    pub(crate) base: usize,
    pub(crate) size: usize,
}

#[cfg(windows)]
pub(crate) fn get_readable_regions(handle: HANDLE) -> Vec<Region> {
    let readable_protect = [
        PAGE_READWRITE.0, PAGE_READONLY.0,
        PAGE_EXECUTE_READ.0, PAGE_EXECUTE_READWRITE.0,
        PAGE_WRITECOPY.0,
    ];

    let mut regions = Vec::with_capacity(512);
    let mut address = 0usize;

    loop {
        if address >= MAX_ADDRESS { break; }
        let mut mbi = MEMORY_BASIC_INFORMATION::default();
        let result = unsafe {
            VirtualQueryEx(
                handle,
                Some(address as *const _),
                &mut mbi,
                std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
            )
        };
        if result == 0 { break; }

        let size = mbi.RegionSize;
        if size == 0 { address += 0x1000; continue; }

        if mbi.State == MEM_COMMIT
            && readable_protect.contains(&mbi.Protect.0)
            && size > 0
            && size <= MAX_REGION_SIZE
        {
            regions.push(Region { base: address, size });
        }

        address = address.saturating_add(size);
    }

    // Smallest-first — active Lua strings in smaller regions
    regions.sort_unstable_by_key(|r| r.size);
    regions
}

#[cfg(not(windows))]
pub(crate) fn get_readable_regions(_handle: ()) -> Vec<Region> { vec![] }


static POOL: std::sync::OnceLock<rayon::ThreadPool> = std::sync::OnceLock::new();

pub(crate) fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

pub(crate) fn get_pool() -> &'static rayon::ThreadPool {
    POOL.get_or_init(|| {
        rayon::ThreadPoolBuilder::new()
            .num_threads(4)
            .thread_name(|i| format!("babelchat-scan-{}", i))
            .start_handler(|_| {
                // The Linux scanner has run its threads at SCHED_IDLE since the
                // day it was written; the Windows one never did, and nobody
                // noticed while the scan was rare. It is not rare: the buffer is
                // a Lua string, so it is reallocated somewhere new on every
                // rebuild, and finding it again is a sweep.
                //
                // THREAD_PRIORITY_IDLE is the same bargain: these threads run
                // only when the game does not want the processor, so the cost
                // lands in the gaps between frames instead of in them.
                #[cfg(windows)]
                unsafe {
                    let _ = SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_IDLE);
                }
            })
            .build()
            .expect("failed to build rayon pool")
    })
}

