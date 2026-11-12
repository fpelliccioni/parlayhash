#include <parlay_hash/unordered_map.h>
#include <cstdint>
#include <thread>
#include <vector>
int main() {
  parlay::parlay_unordered_map<std::uint64_t, std::uint64_t> map(1);
  std::vector<std::thread> workers;
  for (unsigned t = 0; t < 4; ++t)
    workers.emplace_back([&, t] {
      for (unsigned i = 0; i < 100; ++i) map.Insert(t * 100 + i, i);
    });
  for (auto& worker : workers) worker.join();
  if (map.size() != 400) return 1;
  for (unsigned i = 0; i < 400; ++i) {
    auto value = map.Find(i);
    if (!value || *value != i % 100) return 2;
    map.Remove(i);
  }
  return map.size() == 0 ? 0 : 3;
}
