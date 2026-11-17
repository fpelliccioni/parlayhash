#include <parlay_hash/unordered_map.h>
#include <parlay_hash/unordered_set.h>
#include <stdexcept>
template <class T> void assert_(T &&x) {
  if (!x)
    throw std::runtime_error("policy invariant");
}
#include <atomic>
#include <string>
#include <thread>
#include <vector>
struct Hash {
  unsigned salt = 0, mask = 65535;
  size_t operator()(unsigned v) const { return (v & mask) ^ salt; }
};
struct Equal {
  unsigned mask = 65535;
  bool operator()(unsigned a, unsigned b) const {
    return (a & mask) == (b & mask);
  }
};
template <class Map> void check() {
  Map a(1, Hash{19, 65535}, Equal{65535});
  Map b(1, Hash{77, 0xffffff}, Equal{0xffffff});
  for (unsigned i = 0; i < 2000; ++i) {
    a.Insert(i, {});
    b.Insert(i, {});
  }
  for (unsigned i = 0; i < 2000; ++i) {
    assert_(a.Find(i + 65536));
    assert_(!b.Find(i + 65536));
    assert_(a.Remove(i + 65536));
  }
  assert_(a.size() == 0);
}
void traversal() {
  parlay::parlay_unordered_map<unsigned, std::string, Hash, Equal> m(
      1, Hash{31}, Equal{});
  for (unsigned i = 0; i < 32; ++i)
    m.Insert(i, std::to_string(i));
  std::atomic<bool> failed = false;
  std::jthread writer([&] {
    for (unsigned i = 32; i < 10000; ++i) {
      m.Insert(i, std::to_string(i));
      if (i % 2 == 0)
        m.Remove(i);
    }
  });
  for (unsigned round = 0; round < 50; ++round) {
    std::vector<unsigned> seen(32);
    m.ForEach([&](auto const &e) {
      if (e.second != std::to_string(e.first))
        failed = true;
      if (e.first < 32)
        ++seen[e.first];
    });
    for (auto v : seen)
      assert_(v == 1);
  }
  writer.join();
  assert_(!failed);
  try {
    m.ForEach([](auto const &) { throw std::runtime_error("visitor"); });
  } catch (std::runtime_error const &) {
  }
  assert_(epoch::internal::get_epoch().get_my_epoch() == -1);
  m.ForEach([&](auto const &e) {
    m.Find(e.first);
    assert_(epoch::internal::get_epoch().get_my_epoch() != -1);
  });
  assert_(epoch::internal::get_epoch().get_my_epoch() == -1);
}
int main() {
  check<parlay::parlay_unordered_map<unsigned, unsigned, Hash, Equal>>();
  check<parlay::parlay_unordered_map<unsigned, std::string, Hash, Equal>>();
  parlay::parlay_unordered_set<unsigned, Hash, Equal> s(1, Hash{19}, Equal{});
  assert_(s.Insert(1));
  assert_(s.Find(65537));
  assert_(s.Remove(65537));
  traversal();
}
