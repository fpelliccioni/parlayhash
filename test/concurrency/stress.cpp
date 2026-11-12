#include <atomic>
#include <charconv>
#include <exception>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include "parlay_hash/unordered_map.h"

namespace {
constexpr std::uint64_t salt = 0xa0761d6478bd642full;
struct Payload {
  std::uint64_t key, check;
  explicit Payload(std::uint64_t k = 0) : key(k), check(k ^ salt) {}
  bool valid(std::uint64_t k) const { return key == k && check == (k ^ salt); }
};
struct AtomicEqual {
  bool operator()(const Payload& a, const Payload& b) const {
    return a.key == b.key && a.check == b.check;
  }
};

void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

template<class F> void concurrent(F f) {
  constexpr unsigned workers = 8;
  std::atomic<unsigned> ready{0};
  std::atomic<bool> start{false};
  std::vector<std::exception_ptr> failures(workers);
  std::vector<std::thread> threads;
  for (unsigned t = 0; t < workers; ++t) {
    threads.emplace_back([&, t] {
      ready.fetch_add(1, std::memory_order_release);
      while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
      try {
        f(t);
      } catch (...) {
        failures[t] = std::current_exception();
      }
    });
  }
  while (ready.load(std::memory_order_acquire) != workers) std::this_thread::yield();
  start.store(true, std::memory_order_release);
  for (auto& thread : threads) thread.join();
  for (const auto& failure : failures)
    if (failure) std::rethrow_exception(failure);
}

// A small witness for the optimistic snapshot underlying every hash bucket.
void atomic_snapshot() {
  parlay::big_atomic<Payload, AtomicEqual> value(Payload{0});
  std::atomic<bool> bad{false};
  concurrent([&](unsigned t) {
    for (unsigned i = 0; i < 10000; ++i) {
      if (t < 4) {
        auto [old, tag] = value.ll();
        value.sc(tag, Payload{old.key + 1});
      } else {
        auto snapshot = value.load();
        if (!snapshot.valid(snapshot.key)) bad.store(true);
      }
    }
  });
  require(!bad.load(), "torn big_atomic snapshot");
}

template<class Map> void churn() {
  Map map(1);
  std::atomic<long> balance{0};
  std::atomic<bool> bad{false};
  concurrent([&](unsigned t) {
    for (unsigned i = 0; i < 5000; ++i) {
      auto k = static_cast<std::uint64_t>((i * 17 + t * 13) % 64);
      switch ((i + t) % 3) {
      case 0: {
        auto previous = map.Insert(k, Payload{k});
        if (!previous) balance.fetch_add(1);
        else if (!previous->valid(k)) bad.store(true);
        break;
      }
      case 1: {
        auto removed = map.Remove(k);
        if (removed) {
          balance.fetch_sub(1);
          if (!removed->valid(k)) bad.store(true);
        }
        break;
      }
      default: {
        auto found = map.Find(k);
        if (found && !found->valid(k)) bad.store(true);
      }
      }
    }
  });
  require(!bad.load(), "corrupted payload during insert/find/remove");
  require(map.size() == balance.load(), "successful insert/erase balance disagrees with size");
}

// Readers observe stable keys while writers force repeated table forwarding.
// Churn on a separate hot key also exercises epoch retirement and reuse.
template<class Map> void growth() {
  Map map(1);
  for (std::uint64_t k = 0; k < 32; ++k) map.Insert(k, Payload{k});
  std::atomic<bool> bad{false};
  concurrent([&](unsigned t) {
    for (unsigned i = 0; i < 3000; ++i) {
      if (t < 4) {
        std::uint64_t k = 1000 + t * 3000 + i;
        if (map.Insert(k, Payload{k})) bad.store(true);
      } else {
        std::uint64_t k = i % 32;
        auto found = map.Find(k);
        if (!found || !found->valid(k)) bad.store(true);
        map.Insert(50000, Payload{50000});
        auto removed = map.Remove(50000);
        if (removed && !removed->valid(50000)) bad.store(true);
      }
    }
  });
  require(!bad.load(), "lost key or corrupted payload during concurrent growth");
  map.Remove(50000);
  require(map.size() == 12032, "wrong final size after growth");
  for (std::uint64_t k = 1000; k < 13000; ++k) {
    auto found = map.Find(k);
    require(found && found->valid(k), "missing writer key after growth");
  }
}

void upsert() {
  parlay::parlay_unordered_map<std::uint64_t, std::uint64_t> map(1);
  concurrent([&](unsigned) {
    for (unsigned i = 0; i < 2000; ++i) {
      map.Upsert(42, [](auto previous) { return previous.value_or(0) + 1; });
    }
  });
  auto value = map.Find(42);
  require(value && *value == 16000, "lost atomic upsert increments");
  require(map.size() == 1, "duplicate key after concurrent upserts");
}

// Heap-owning values force the indirect path and exercise copy/destruction
// while competing updates retire old versions of the same entry.
struct CollidingHash {
  std::size_t operator()(std::uint64_t) const { return 0; }
};

template<class Hash = std::hash<std::uint64_t>>
void string_upsert(bool overflow = false) {
  using Map = parlay::parlay_unordered_map<std::uint64_t, std::string, Hash>;
  Map map(1);
  const std::string prefix(128, 'x');
  if (overflow) {
    using Impl = typename Map::map;
    for (unsigned i = 0; i < Impl::buffer_size; ++i) map.Insert(i, prefix + "0");
    map.Insert(42, prefix + "0");
    map.Insert(43, prefix + "0");
    map.Insert(44, prefix + "0");
  }
  std::atomic<bool> bad{false};
  concurrent([&](unsigned) {
    for (unsigned i = 0; i < 1000; ++i) {
      map.Upsert(42, [&](auto previous) {
        unsigned long count = 0;
        if (previous) {
          if (previous->size() <= prefix.size() ||
              previous->compare(0, prefix.size(), prefix) != 0) {
            bad.store(true);
            return prefix + "0";
          }
          const auto end = previous->data() + previous->size();
          auto parsed = std::from_chars(previous->data() + prefix.size(), end, count);
          if (parsed.ec != std::errc{} || parsed.ptr != end) {
            bad.store(true);
            return prefix + "0";
          }
        }
        return prefix + std::to_string(count + 1);
      });
      auto found = map.Find(42);
      if (!found || found->compare(0, prefix.size(), prefix)) bad.store(true);
    }
  });
  require(!bad.load(), "corrupted heap-owning value during upsert/find");
  auto value = map.Find(42);
  require(value && *value == prefix + "8000", "lost string upsert increments");
  if (overflow) {
    require(map.Find(43) == prefix + "0" && map.Find(44) == prefix + "0",
            "upsert retired an unchanged overflow entry");
  }
}
}

int main(int argc, char** argv) {
  try {
    require(argc == 2, "provide a scenario name");
    const std::string scenario(argv[1]);
    using Direct = parlay::parlay_unordered_map_direct<std::uint64_t, Payload>;
    using Indirect = parlay::parlay_unordered_map_indirect<std::uint64_t, Payload>;
    if (scenario == "atomic_snapshot") atomic_snapshot();
    else if (scenario == "direct_churn") churn<Direct>();
    else if (scenario == "indirect_churn") churn<Indirect>();
    else if (scenario == "direct_growth") growth<Direct>();
    else if (scenario == "indirect_growth") growth<Indirect>();
    else if (scenario == "upsert") upsert();
    else if (scenario == "string_upsert") string_upsert();
    else if (scenario == "string_overflow_upsert") string_upsert<CollidingHash>(true);
    else throw std::runtime_error("unknown scenario");
    std::cout << "PASS " << scenario << '\n';
  } catch (const std::exception& error) {
    std::cerr << "FAIL " << (argc > 1 ? argv[1] : "unknown") << ": " << error.what() << '\n';
    return 1;
  }
}
