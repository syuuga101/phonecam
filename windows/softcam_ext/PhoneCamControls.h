#pragma once
// PhoneCam extension for softcam: camera controls + property pages.
//
// Adds IAMVideoProcAmp / IAMCameraControl / ISpecifyPropertyPages to the filter so that the
// standard Windows property pages (CLSID_VideoProcAmpPropertyPage / CLSID_CameraControlPropertyPage)
// open from OBS "Configure Video". Values live in a small named shared-memory block that
// windows/phonecam_dshowctl.py (the PC receiver) reads/writes.
//
// Shared memory "PhoneCam/Controls" (4096 bytes), named mutex "PhoneCam/ControlsMutex". Little endian.
//   Header (64 bytes):  u32 magic 'PCCT'(0x54434350) | u32 version(1) | u32 request_seq |
//                       u32 ack_seq | u32 state_seq | u32 slot_count(8) | u32 slot_size(64) | u32 reserved | pad
//   Slot i at 64 + 64*i: i32 min,max,step,default,value,flags,caps,supported | u32 changed_seq | i32 reserved[7]
//   flags / caps use the DirectShow values: Auto = 1, Manual = 2 (caps may be 3).
//   Slots: 0 Brightness 1 Contrast 2 Saturation 3 Sharpness 4 WhiteBalance 5 Zoom 6 Focus 7 Exposure
// The filter (this file) bumps request_seq and slot.changed_seq on every Set(); the receiver consumes
// changes with changed_seq > ack_seq and then stores ack_seq = request_seq.
// A block whose magic is 0 (freshly created mapping) is initialised with the defaults below by whichever
// side gets the mutex first.

#include <cstdint>
#include <cstring>

namespace phonecam {

static const uint32_t kMagic      = 0x54434350u;   // 'P','C','C','T'
static const uint32_t kVersion    = 1;
static const DWORD    kBlockSize  = 4096;
static const int      kSlotCount  = 8;
static const int      kSlotSize   = 64;
static const int      kHeaderSize = 64;
static const LONG     kFlagAuto   = 1;
static const LONG     kFlagManual = 2;

enum SlotId
{
    SlotBrightness = 0, SlotContrast, SlotSaturation, SlotSharpness,
    SlotWhiteBalance, SlotZoom, SlotFocus, SlotExposure
};

struct CtlHeader
{
    uint32_t magic, version, request_seq, ack_seq, state_seq, slot_count, slot_size, reserved;
    uint8_t  pad[32];
};
struct CtlSlot
{
    int32_t  vmin, vmax, step, def, value, flags, caps, supported;
    uint32_t changed_seq;
    int32_t  reserved[7];
};
struct CtlBlock
{
    CtlHeader h;
    CtlSlot   s[kSlotCount];
};
static_assert(sizeof(CtlHeader) == kHeaderSize, "CtlHeader size");
static_assert(sizeof(CtlSlot) == kSlotSize, "CtlSlot size");
static_assert(sizeof(CtlBlock) <= 4096, "CtlBlock size");

// block must be zeroed beforehand
inline void FillDefaults(CtlBlock* b)
{
    //                      min   max  step  def  value flags caps sup
    const int32_t d[kSlotCount][8] = {
        {  -50,   50,   1,    0,    0,  2, 2, 1 },   // Brightness  (-0.50..0.50)
        {   50,  150,   1,  100,  100,  2, 2, 1 },   // Contrast    (0.5..1.5)
        {    0,  200,   1,  100,  100,  2, 2, 1 },   // Saturation  (0..2)
        {    0,  100,   1,    0,    0,  2, 2, 1 },   // Sharpness   (0..1)
        { 2500, 8000,  50, 5000, 5000,  1, 3, 1 },   // WhiteBalance (K, Auto/Manual)
        {  100, 1000,   1,  100,  100,  2, 2, 1 },   // Zoom        (1.0x..10.0x)
        {    0,  100,   1,   50,   50,  1, 3, 1 },   // Focus       (0..1, Auto/Manual)
        {  -13,   -1,   1,   -7,   -7,  1, 3, 1 },   // Exposure    (log2 seconds, Auto/Manual)
    };
    for (int i = 0; i < kSlotCount; ++i)
    {
        CtlSlot& s = b->s[i];
        s.vmin = d[i][0]; s.vmax = d[i][1]; s.step = d[i][2]; s.def = d[i][3];
        s.value = d[i][4]; s.flags = d[i][5]; s.caps = d[i][6]; s.supported = d[i][7];
        s.changed_seq = 0;
    }
    b->h.version = kVersion;
    b->h.slot_count = (uint32_t)kSlotCount;
    b->h.slot_size = (uint32_t)kSlotSize;
    b->h.magic = kMagic;   // last
}

// Access to the block. Falls back to a private in-memory copy if the shared one cannot be used
// (the dialog still opens; changes just do not reach the receiver).
class ControlBlock
{
public:
    ControlBlock() : m_mutex(nullptr), m_map(nullptr), m_view(nullptr), m_held(false),
                     m_tried(false), m_lastTry(0)
    {
        std::memset(&m_local, 0, sizeof(m_local));
        FillDefaults(&m_local);
    }
    ~ControlBlock()
    {
        if (m_view) UnmapViewOfFile(m_view);
        if (m_map) CloseHandle(m_map);
        if (m_mutex) CloseHandle(m_mutex);
    }

    HRESULT getRange(int slot, LONG* mn, LONG* mx, LONG* st, LONG* df, LONG* caps)
    {
        if (!mn || !mx || !st || !df || !caps) return E_POINTER;
        CAutoLock lk(&m_cs);
        Guard g(this);
        const CtlSlot& s = g.b->s[slot];
        if (!s.supported) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        *mn = s.vmin; *mx = s.vmax; *st = s.step > 0 ? s.step : 1; *df = s.def; *caps = s.caps;
        return S_OK;
    }

    HRESULT get(int slot, LONG* value, LONG* flags)
    {
        if (!value || !flags) return E_POINTER;
        CAutoLock lk(&m_cs);
        Guard g(this);
        const CtlSlot& s = g.b->s[slot];
        if (!s.supported) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        *value = s.value;
        *flags = (s.flags == kFlagAuto) ? kFlagAuto : kFlagManual;
        return S_OK;
    }

    HRESULT set(int slot, LONG value, LONG flags)
    {
        CAutoLock lk(&m_cs);
        Guard g(this);
        CtlSlot& s = g.b->s[slot];
        if (!s.supported) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        if (flags == kFlagAuto)
        {
            if (!(s.caps & kFlagAuto)) return E_INVALIDARG;
            s.flags = kFlagAuto;                    // keep the value
        }
        else if (flags == kFlagManual)
        {
            if (!(s.caps & kFlagManual)) return E_INVALIDARG;
            if (value < s.vmin) value = s.vmin;
            if (value > s.vmax) value = s.vmax;
            s.value = value;
            s.flags = kFlagManual;
        }
        else
        {
            return E_INVALIDARG;
        }
        g.b->h.request_seq += 1;
        s.changed_seq = g.b->h.request_seq;
        return S_OK;
    }

private:
    struct Guard
    {
        ControlBlock* c;
        CtlBlock*     b;
        explicit Guard(ControlBlock* cb) : c(cb), b(cb->acquire()) {}
        ~Guard() { c->release(); }
    };

    CCritSec  m_cs;
    HANDLE    m_mutex;
    HANDLE    m_map;
    CtlBlock* m_view;
    bool      m_held;
    bool      m_tried;
    DWORD     m_lastTry;
    CtlBlock  m_local;

    bool ensure()
    {
        if (m_view) return true;
        DWORD now = GetTickCount();
        if (m_tried && (DWORD)(now - m_lastTry) < 1000) return false;
        m_tried = true;
        m_lastTry = now;

        // NULL DACL: the receiver and OBS may run at different integrity levels.
        SECURITY_DESCRIPTOR sd;
        SECURITY_ATTRIBUTES sa;
        SECURITY_ATTRIBUTES* psa = nullptr;
        if (InitializeSecurityDescriptor(&sd, SECURITY_DESCRIPTOR_REVISION) &&
            SetSecurityDescriptorDacl(&sd, TRUE, nullptr, FALSE))
        {
            sa.nLength = sizeof(sa);
            sa.lpSecurityDescriptor = &sd;
            sa.bInheritHandle = FALSE;
            psa = &sa;
        }
        if (!m_mutex) m_mutex = CreateMutexW(psa, FALSE, L"PhoneCam/ControlsMutex");
        if (!m_mutex) return false;
        if (!m_map) m_map = CreateFileMappingW(INVALID_HANDLE_VALUE, psa, PAGE_READWRITE, 0, kBlockSize,
                                               L"PhoneCam/Controls");   // returns the existing one if present
        if (!m_map) return false;
        void* p = MapViewOfFile(m_map, FILE_MAP_ALL_ACCESS, 0, 0, kBlockSize);
        if (!p) return false;
        m_view = static_cast<CtlBlock*>(p);
        return true;
    }

    // Called with m_cs held. Returns the block to use; release() must follow.
    CtlBlock* acquire()
    {
        m_held = false;
        if (ensure())
        {
            DWORD w = WaitForSingleObject(m_mutex, 1000);
            if (w == WAIT_OBJECT_0 || w == WAIT_ABANDONED)
            {
                m_held = true;
                if (m_view->h.magic == 0)
                {
                    std::memset(m_view, 0, kBlockSize);
                    FillDefaults(m_view);
                }
                if (m_view->h.magic == kMagic && m_view->h.version == kVersion &&
                    m_view->h.slot_count == (uint32_t)kSlotCount && m_view->h.slot_size == (uint32_t)kSlotSize)
                {
                    return m_view;
                }
                ReleaseMutex(m_mutex);
                m_held = false;
            }
        }
        return &m_local;
    }

    void release()
    {
        if (m_held)
        {
            ReleaseMutex(m_mutex);
            m_held = false;
        }
    }
};

// ---- COM wrappers (separate objects because IAMVideoProcAmp and IAMCameraControl have identical
//      method signatures; they delegate their IUnknown to the filter) ----

class ProcAmpIface : public IAMVideoProcAmp
{
public:
    ProcAmpIface(LPUNKNOWN owner, ControlBlock* cb) : m_owner(owner), m_cb(cb) {}

    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override { return m_owner->QueryInterface(riid, ppv); }
    STDMETHODIMP_(ULONG) AddRef() override { return m_owner->AddRef(); }
    STDMETHODIMP_(ULONG) Release() override { return m_owner->Release(); }

    STDMETHODIMP GetRange(long Property, long* pMin, long* pMax, long* pSteppingDelta,
                          long* pDefault, long* pCapsFlags) override
    {
        int s = slot(Property);
        if (s < 0) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        return m_cb->getRange(s, pMin, pMax, pSteppingDelta, pDefault, pCapsFlags);
    }
    STDMETHODIMP Set(long Property, long lValue, long Flags) override
    {
        int s = slot(Property);
        if (s < 0) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        return m_cb->set(s, lValue, Flags);
    }
    STDMETHODIMP Get(long Property, long* lValue, long* Flags) override
    {
        int s = slot(Property);
        if (s < 0) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        return m_cb->get(s, lValue, Flags);
    }

private:
    LPUNKNOWN     m_owner;
    ControlBlock* m_cb;

    static int slot(long property)
    {
        switch (property)
        {
        case VideoProcAmp_Brightness:   return SlotBrightness;
        case VideoProcAmp_Contrast:     return SlotContrast;
        case VideoProcAmp_Saturation:   return SlotSaturation;
        case VideoProcAmp_Sharpness:    return SlotSharpness;
        case VideoProcAmp_WhiteBalance: return SlotWhiteBalance;
        default:                        return -1;
        }
    }
};

class CameraControlIface : public IAMCameraControl
{
public:
    CameraControlIface(LPUNKNOWN owner, ControlBlock* cb) : m_owner(owner), m_cb(cb) {}

    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override { return m_owner->QueryInterface(riid, ppv); }
    STDMETHODIMP_(ULONG) AddRef() override { return m_owner->AddRef(); }
    STDMETHODIMP_(ULONG) Release() override { return m_owner->Release(); }

    STDMETHODIMP GetRange(long Property, long* pMin, long* pMax, long* pSteppingDelta,
                          long* pDefault, long* pCapsFlags) override
    {
        int s = slot(Property);
        if (s < 0) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        return m_cb->getRange(s, pMin, pMax, pSteppingDelta, pDefault, pCapsFlags);
    }
    STDMETHODIMP Set(long Property, long lValue, long Flags) override
    {
        int s = slot(Property);
        if (s < 0) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        return m_cb->set(s, lValue, Flags);
    }
    STDMETHODIMP Get(long Property, long* lValue, long* Flags) override
    {
        int s = slot(Property);
        if (s < 0) return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
        return m_cb->get(s, lValue, Flags);
    }

private:
    LPUNKNOWN     m_owner;
    ControlBlock* m_cb;

    static int slot(long property)
    {
        switch (property)
        {
        case CameraControl_Zoom:     return SlotZoom;
        case CameraControl_Focus:    return SlotFocus;
        case CameraControl_Exposure: return SlotExposure;
        default:                     return -1;
        }
    }
};

// Owned by the Softcam filter. owner = filter->GetOwner() (controlling IUnknown).
class CameraControls
{
public:
    explicit CameraControls(LPUNKNOWN owner) : m_procamp(owner, &m_block), m_camctl(owner, &m_block) {}
    IAMVideoProcAmp*   procAmp()       { return &m_procamp; }
    IAMCameraControl*  cameraControl() { return &m_camctl; }

private:
    ControlBlock       m_block;       // must be declared before the two interfaces
    ProcAmpIface       m_procamp;
    CameraControlIface m_camctl;
    CameraControls(const CameraControls&);
    CameraControls& operator=(const CameraControls&);
};

// ---- ISpecifyPropertyPages ----

// {71F96464-78F3-11D0-A18C-00A0C9118956} CLSID_VideoProcAmpPropertyPage
static const GUID kProcAmpPage =
    { 0x71F96464, 0x78F3, 0x11D0, { 0xA1, 0x8C, 0x00, 0xA0, 0xC9, 0x11, 0x89, 0x56 } };
// {71F96465-78F3-11D0-A18C-00A0C9118956} CLSID_CameraControlPropertyPage
static const GUID kCameraControlPage =
    { 0x71F96465, 0x78F3, 0x11D0, { 0xA1, 0x8C, 0x00, 0xA0, 0xC9, 0x11, 0x89, 0x56 } };

inline bool PageRegistered(const GUID& clsid)
{
    WCHAR g[64];
    if (StringFromGUID2(clsid, g, 64) <= 0) return false;
    WCHAR key[160];
    wsprintfW(key, L"CLSID\\%s\\InprocServer32", g);
    HKEY h = nullptr;
    if (RegOpenKeyExW(HKEY_CLASSES_ROOT, key, 0, KEY_READ, &h) == ERROR_SUCCESS)
    {
        RegCloseKey(h);
        return true;
    }
    return false;
}

inline HRESULT FillPropertyPages(CAUUID* p)
{
    if (!p) return E_POINTER;
    p->cElems = 0;
    p->pElems = nullptr;
    GUID pages[2];
    ULONG n = 0;
    pages[n++] = kProcAmpPage;
    if (PageRegistered(kCameraControlPage)) pages[n++] = kCameraControlPage;
    GUID* mem = static_cast<GUID*>(CoTaskMemAlloc(sizeof(GUID) * n));
    if (!mem) return E_OUTOFMEMORY;
    for (ULONG i = 0; i < n; ++i) mem[i] = pages[i];
    p->cElems = n;
    p->pElems = mem;
    return S_OK;
}

} // namespace phonecam
