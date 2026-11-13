// An implementation of big_atomic using a SeqLock.
//
//  Supports:
//  - Blocking loads (loads never obstruct each-other, but can be indefinitely blocked by writers)
//  - Blocking stores
//  - Blocking CAS
//
// Atomic payload storage preserves optimistic readers and serialized writers.
//

#ifndef PARLAYATOMIC_H_
#define PARLAYATOMIC_H_

#include <atomic>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <thread>
#include <functional>
#include <parlay/primitives.h>
#include <parlay/sequence.h>
#include <utils/lock.h>

namespace parlay {

// Atomic representations are read before validation; V is reconstructed only
// after the version check. Pair members are encoded separately because std::pair
// need not be trivially copyable even when both of its members are.
namespace detail {
template<class V, class = void>
struct snapshot_storage {
  static_assert(std::is_trivially_copyable_v<V>);
  // Do not promote indeterminate padding bytes into multi-byte integers. Types without
  // unique object representations use uninitialized-friendly byte storage.
  using word = std::conditional_t<!std::has_unique_object_representations_v<V>, unsigned char,
    std::conditional_t<(alignof(V) >= 8 && sizeof(V) % 8 == 0), std::uint64_t,
    std::conditional_t<(alignof(V) >= 4 && sizeof(V) % 4 == 0), std::uint32_t,
    std::conditional_t<(alignof(V) >= 2 && sizeof(V) % 2 == 0), std::uint16_t, std::uint8_t>>>>;
  static_assert(std::atomic<word>::is_always_lock_free);
  using snapshot_type = std::array<word, sizeof(V) / sizeof(word)>;
  std::array<std::atomic<word>, sizeof(V) / sizeof(word)> words{};

  void store(const V& value) {
    auto bits = std::bit_cast<snapshot_type>(value);
    for (std::size_t i = 0; i < words.size(); ++i)
      words[i].store(bits[i], std::memory_order_relaxed);
  }
  snapshot_type read() const {
    snapshot_type bits;
    for (std::size_t i = 0; i < words.size(); ++i)
      bits[i] = words[i].load(std::memory_order_relaxed);
    return bits;
  }
  static V decode(const snapshot_type& bits) { return std::bit_cast<V>(bits); }
};

template<class A, class B>
struct snapshot_storage<std::pair<A, B>> {
  using first_storage = snapshot_storage<A>;
  using second_storage = snapshot_storage<B>;
  using snapshot_type = std::pair<typename first_storage::snapshot_type,
                                  typename second_storage::snapshot_type>;
  first_storage first;
  second_storage second;
  void store(const std::pair<A, B>& value) {
    first.store(value.first);
    second.store(value.second);
  }
  snapshot_type read() const { return {first.read(), second.read()}; }
  static std::pair<A, B> decode(const snapshot_type& bits) {
    return {first_storage::decode(bits.first), second_storage::decode(bits.second)};
  }
};

template<class V>
struct snapshot_storage<V, std::void_t<typename V::parlay_snapshot_storage>> : V::parlay_snapshot_storage {};
}  // namespace detail



template<typename V, class KeyEqual = std::equal_to<V>>
struct alignas(32) big_atomic {

  using vtype = std::uint64_t;
  using tag = vtype;

  std::atomic<vtype> version;
  detail::snapshot_storage<V> val;

  big_atomic(const V& v) : version(0) { val.store(v); }
  big_atomic() : big_atomic(V{}) {}

  void store_sequential(const V& v) { val.store(v); }
  
  V load() { return ll().first; }

  // An unvalidated representation may contain torn pointers or bools. Keep the
  // legacy entry point, but never expose such a representation as a V.
  std::pair<V,tag> ll_speculative() { return ll(); }

  PARLAY_INLINE std::pair<V,tag> ll() {
    unsigned retries = 0;
    while (true) {
      vtype ver = version.load(std::memory_order_acquire);
      if ((ver & 1) == 0) {
        auto bits = val.read();
        std::atomic_thread_fence(std::memory_order_acquire);
        if (version.load(std::memory_order_relaxed) == ver)
          return {decltype(val)::decode(bits), ver};
      }
      // Keep the uncontended path read-only and let stalled writers progress
      // after a bounded number of failed observations.
      if (++retries == 64) {
        retries = 0;
        std::this_thread::yield();
      }
    }
  }

  bool lv(tag tg) {
    return version.load() == tg;
  }

  bool sc(tag expected_tag, const V& v) {
    bool result = true;
    int delay = 100;
    while (true) {
      vtype ver = version.load();
      if (ver != expected_tag) return false;
      if (get_locks().try_lock((size_t)this, [&] {
            if (version.load(std::memory_order_acquire) != expected_tag)
              result = false;
            else {
              version.store(ver + 1, std::memory_order_relaxed);
              std::atomic_thread_fence(std::memory_order_release);
              val.store(v);
              version.store(ver + 2, std::memory_order_release);
            }
            return true;
          }))
        return result;
      for (volatile int i = 0; i < delay; i++);
      delay = std::min(2 * delay, 2000);
    }
  }

};

}  // namespace parlay
#endif  // PARLAYATOMIC_H_
