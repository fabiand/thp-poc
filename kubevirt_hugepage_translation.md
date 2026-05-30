# KubeVirt Hugepage Translation Approach

**Goal:**
Deliver intent-based memory allocation. Users request hugepages. Platform decides whether to use static HugeTLB or transparent hugepages (THP) with preallocation and mlock.

**The Problem:**
* Static hugepages require rigid node partitioning.
* Runtime fallback (waiting for Pod `Pending` state to timeout) breaks Kubernetes immutability. You can't change pod resource requests on the fly.

**The Solution (Translation at Submission):**
* **Cluster Switch:** Add `translateHugepagesToMadvise: true` to the KubeVirt CR.
* **Intercept:** Mutating Admission Webhook catches the VM creation request.
* **Translate Pod:** Strip the `hugepages-2Mi` request. Replace with standard `memory` request. Scheduler handles it normally.
* **Inject XML:** Configure `virt-launcher` (via controller or hook sidecar) to inject the following Libvirt XML:
  * `<locked/>` (mlock)
  * `<allocation mode='immediate'/>` (prealloc)
  * `<source type='anonymous'/>` (THP trigger)

**Why preallocation is critical for THP:**
* **Beat Fragmentation:** `mmap` and `madvise` are lazy. Preallocation forces the kernel to allocate 2MB blocks immediately at boot when memory is cleanest, rather than relying on runtime page faults when the node is already fragmented.
* **Dense Mapping:** Touching every 4KB page boundary prevents partial mappings. It guarantees the entire 2MB block is fully populated and backed by physical RAM with zero lazy allocation overhead.
* **Decouple Lock Contention:** Populating physical pages via a touch loop first prevents the kernel from choking on lock contention if it tries to allocate and `mlock` raw virtual ranges under a single heavy lock context.

**Why mlock is critical for THP:**
* **Anti-Splitting:** Prevents the kernel from splitting pristine 2MB pages back to 4KB pages under heavy host memory pressure.
* **TLB Anchor:** Permanently pins the physical-to-virtual path. Keeps high-value 2MB TLB cache entries completely hot.
* **Kill Background Stalls:** Forces immediate materialization at allocation. Removes the memory region from `khugepaged` scanning, wiping out background daemon CPU jitter.

**Smart Fallback via Hook Sidecar:**
* Run a hook sidecar in the `virt-launcher` pod before QEMU boots.
* Read `/proc/buddyinfo` to verify contiguous 2MB blocks exist.
* **If available:** Inject the XML. QEMU handles mmap -> madvise -> prealloc -> mlock in order.
* **If unavailable:** Skip XML injection. QEMU falls back to standard 4KB pages. Prevents locking fragmented 4KB pages and causing db stalls.

**Why this works:**
* Clean UX. Users just ask for hugepages.
* Zero custom scheduling logic in KubeVirt. `kube-scheduler` tracks standard RAM.
* Smooth live migrations (standard RAM vs rigid HugeTLB pools).
