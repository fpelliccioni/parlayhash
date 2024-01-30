// Initial Author: Guy Blelloch
// Developed as part of the flock library
// 
// A growable unordered_map using a hash table designed for scalability to large number of threads, and
// for high contention.  On a key type K and value type V it supports:
//
//   unordered_map<K, V, Hash=std::hash<K>, Equal=std::equal_to<K>>(n) :
//   constructor for table of initial size n
//
//   Find(const K&) -> std::optional<V> :
//   returns value if key is found, and otherwise returns nullopt
//
//   Insert(const K&, const V&) -> std::optional<V> :
//   if key not in the table it inserts the key with the given value
//   and returns nullopt, otherwise it does not modify the table and
//   returns the old value.
//
//   Remove(const K&) -> std::optional<V> :
//   if key is in the table it removes the entry and returns its value.
//   otherwise it does nothing and returns nullopt.
//
//   size() -> long : returns the size of the table.  Not linearizable with
//   the other functions, and takes time proportional to the table size.
//  
//   clear() -> void : clears the table so its size is 0.
//
//   for_each(F f) : applies functor f to each entry of the table.
//   f should be of type (const std::pair<K,V>&) -> void

#ifndef PARLAY_UNORDERED_MAP_
#define PARLAY_UNORDERED_MAP_

#include <functional>
#include <optional>
#include "parlay_hash.h"
#include <parlay/sequence.h>
#include <utils/epoch.h>

namespace parlay {
  static constexpr bool default_clear_at_end = false;

  // conditionally rehash if typy Hash::avalanching is not defined
  template<typename Hash, typename ignore = void>
  struct rehash {
    size_t operator()(size_t h) {
      size_t x = h * UINT64_C(0xbf58476d1ce4e5b9); // linear transform
      return (x ^ (x >> 31));  // non-linear transform
    }};

  template<typename Hash>
  struct rehash<Hash, typename Hash::is_avalanching> {
    size_t operator()(size_t i) {return i;}};

  template <typename K_, typename V_, class Hash_ = std::hash<K_>, class KeyEqual_ = std::equal_to<K_>>
  struct MapData {
    using K = K_;
    using V = V_;
    using Hash = Hash_;
    using KeyEqual = KeyEqual_;
    using value_type = std::pair<K,V>;
    static const K& get_key(const value_type& x) { return x.first;}
  };

  template <typename K_, class Hash_ = std::hash<K_>, class KeyEqual_ = std::equal_to<K_>>
  struct SetData {
    using K = K_;
    using Hash = Hash_;
    using KeyEqual = KeyEqual_;
    using value_type = K;
    static const K& get_key(const value_type& x) { return x;}
  };

  // Definition where entries of the hash table are stored indirectly
  // through a pointer.  This means the entries themselves will never
  // move, but requires a level of indirection when accessing them.
  // Tags the high-bits of pointers with part of the hash function so
  // one can avoid the indirection if the tags do not match.
  // Currently used for all types that are not trivially copyable.
  template <typename EntryData>
  struct IndirectEntries {
    using DataS = EntryData;
    using Data = typename DataS::value_type;
    using Hash = typename DataS::Hash;
    using KeyEqual = typename DataS::KeyEqual;
      
    struct Entry {
      using K = typename DataS::K;
      using Key = std::pair<const K*,size_t>;
      static constexpr bool Direct = false;
      Data* ptr;
      static Data* tag_ptr(size_t hashv, Data* data) {
	return (Data*) (((hashv >> 48) << 48) | ((size_t) data));
      }
      Data* get_ptr() const {
	return (Data*) (((size_t) ptr) & ((1ul << 48) - 1)); }
      static unsigned long hash(const Key& k) {
	return k.second;}
      bool equal(const Key& k) const {
	return (((k.second >> 48) == (((size_t) ptr) >> 48)) &&
		KeyEqual{}(DataS::get_key(*get_ptr()), *k.first)); }
      Key get_key() const { return make_key(DataS::get_key(*get_ptr()));}
      Data& get_entry() const { return *get_ptr();}
      static Key make_key(const K& key) {
	return Key(&key, rehash<Hash>{}(Hash{}(key)));}
      Entry(Key k, Data* data) : ptr(tag_ptr(hash(k), data)) {}
      Entry() {}
    };

    bool clear_at_end;
    using Key = typename Entry::Key;

    // a memory pool for the entries
    epoch::memory_pool<Data>* data_pool;

    IndirectEntries(bool clear_at_end=false) 
      : clear_at_end(clear_at_end),
	data_pool(clear_at_end ?
		  new epoch::memory_pool<Data>() :
		  &epoch::get_default_pool<Data>()) {}
    ~IndirectEntries() {
      if (clear_at_end) { delete data_pool;}
    }

    // allocates memory for the entry
    Entry make_entry(const Key& k, const Data& data) {
      return Entry(k, data_pool->New(data)); }

    // retires the memory for the entry
    void retire_entry(Entry& e) {
      data_pool->Retire(e.get_ptr()); }
  };

  // Definition where entries of the hash table are stored directly.
  // This means the entries might be moved during updates, including
  // insersions, removals, and resizing.  Currently used for trivially
  // copyable types.
  template <typename EntryData>
  struct DirectEntries {
    using DataS = EntryData;
    using Data = typename DataS::value_type;
    using Hash = typename DataS::Hash;
    using KeyEqual = typename DataS::KeyEqual;
    using K = typename DataS::K;

    struct Entry {
      using K = typename DataS::K;
      using Key = K;
      static const bool Direct = true;
      Data data;
      static unsigned long hash(const Key& k) {
	return rehash<Hash>{}(Hash{}(k));}
      bool equal(const Key& k) const { return KeyEqual{}(get_key(), k); }
      static Key make_key(const K& k) {return k;}
      const K& get_key() const {return DataS::get_key(data);}
      const Data& get_entry() const { return data;}
      Entry(const Data& data) : data(data) {}
      Entry() {}
    };

    DirectEntries(bool clear_at_end=false) {}
    Entry make_entry(const K& k, const Data& data) {
      return Entry(data); }

    // retiring is a noop since no memory has been allocated for entries
    void retire_entry(Entry& e) {}
  };

  // Generic unordered_map that can be used with direct or indirect
  // entries depending on the template argument.
  template <typename Entries>
  struct unordered_map_ {
    using map = parlay_hash<Entries>;

    Entries entries_;
    map m;

    using Entry = typename Entries::Entry;
    using K = typename Entries::DataS::K;
    using V = typename Entries::DataS::V;
    using key_type = K;
    using mapped_type = V;
    using value_type = std::pair<K, V>;
    using iterator = typename map::Iterator;

    static constexpr auto true_f = [] (const Entry& kv) {return true;};
    static constexpr auto identity = [] (const Entry& kv) {return kv;};
    static constexpr auto get_value = [] (const value_type& kv) {return kv.second;};

    unordered_map_(long n, bool clear_at_end = default_clear_at_end)
      : entries_(Entries(clear_at_end)),
	m(map(n, &entries_, clear_at_end)) {}
    
    iterator begin() { return m.begin();}
    iterator end() { return m.end();}
    bool empty() { return size() == 0;}
    bool max_size() { return (1ul << 47)/sizeof(Entry);}
    void clear() { m.clear_buckets();}
    long size() { return m.size();}

    template <typename F = decltype(identity)>
    auto entries(const F& f = identity) { return m.entries(f);}
    long count(const K& k) { return (contains(k)) ? 1 : 0; }
    bool contains(const K& k) { return find(k, true_f).has_value();}

    template <typename F = decltype(get_value)>
    auto Find(const K& k, const F& f = get_value)
      -> std::optional<typename std::result_of<F(value_type)>::type>
    {
      auto g = [&] (const Entry& e) {return f(e.get_entry());};
      return m.Find(Entry::make_key(k), g);
    }

    auto Findx(const K& k) -> std::optional<mapped_type>
    { auto x = m.find(Entry::make_key(k));
      if (x == end()) return {};
      else return (*x).second;
    }

    auto Insert(const K& key, const V& value) -> std::optional<mapped_type>
    {
      auto k = Entry::make_key(key);
      auto g = [&] (const Entry& e) {return get_value(e.get_entry());};
      return m.Insert(k, [&] {return entries_.make_entry(k, value_type(key, value));}, g);
    }

    template <typename F>
    auto Upsert(const K& key, const F& f) -> std::optional<mapped_type>
    {
      auto k = Entry::make_key(key);
      auto g = [&] (const Entry& e) {return get_value(e.get_entry());};
      auto constr = [&] (const std::optional<Entry>& e) -> Entry {
		      if (e.has_value())
			return entries_.make_entry(k, value_type(key, f(std::optional(get_value((*e).get_entry())))));
		      return entries_.make_entry(k, value_type(key, f(std::optional<V>())));
		    };
      return m.Upsert(k, constr, g);
    }

    template <typename F>
    auto Insert(const K& key, const V& value, const F& f)
      -> std::optional<typename std::result_of<F(value_type)>::type>
    {
      auto k = Entry::make_key(key);
      auto g = [&] (const Entry& e) {return f(e.get_entry());};
      return m.Insert(k, [&] {return entries_.make_entry(k, value_type(key, value));}, g);
    }

    auto Remove(const K& k) -> std::optional<mapped_type>
    {
      auto g = [&] (const Entry& e) {return get_value(e.get_entry());};
      return m.Remove(Entry::make_key(k), g);
    }

    template <typename F>
    auto Remove(const K& k, const F& f)
      -> std::optional<typename std::result_of<F(value_type)>::type>
    {
      auto g = [&] (const Entry& e) {return f(e.get_entry());};
      return m.Remove(Entry::make_key(k), g);
    }

    iterator find(const K& k) { return m.find(k); }

    std::pair<iterator,bool> insert(const value_type& entry) {
      auto k = Entry::make_key(entry.first);
      return m.insert(k, [&] {return entries_.make_entry(k, entry);});}

    iterator erase(iterator pos) { return m.erase(pos); }
    size_t erase(const K& k) { return m.erase(k); }

  };

  // specialization of unordered_map to use either direct or indirect
  // entries depending on whether K and V are trivially copyable.
  template <typename K, typename V, class Hash = std::hash<K>, class KeyEqual = std::equal_to<K>>
  using parlay_unordered_map = std::conditional_t<std::is_trivially_copyable_v<K> &&
						  std::is_trivially_copyable_v<V>,
						  unordered_map_<DirectEntries<MapData<K, V, Hash, KeyEqual>>>,
						  unordered_map_<IndirectEntries<MapData<K, V, Hash, KeyEqual>>>>;

  // Generic unordered_set that can be used with direct or indirect
  // entries depending on the template argument.
  template <typename Entries>
  struct unordered_set_ {
    using set = parlay_hash<Entries>;

    Entries entries_;
    set m;

    using Entry = typename Entries::Entry;
    using K = typename Entries::DataS::K;
    using key_type = K;
    using value_type = K;
    using iterator = typename set::Iterator;

    static constexpr auto true_f = [] (const Entry& kv) {return true;};
    static constexpr auto identity = [] (const Entry& kv) {return kv;};

    unordered_set_(long n, bool clear_at_end = default_clear_at_end)
      : entries_(Entries(clear_at_end)),
	m(set(n, &entries_, clear_at_end)) {}
    
    iterator begin() { return m.begin();}
    iterator end() { return m.end();}
    bool empty() { return size() == 0;}
    bool max_size() { return (1ul << 47)/sizeof(Entry);}
    void clear() { m.clear_buckets();}
    long size() { return m.size();}

    template <typename F = decltype(identity)>
    auto entries(const F& f = identity) { return m.entries(f);}
    long count(const K& k) { return (contains(k)) ? 1 : 0; }
    bool contains(const K& k) { return find(k, true_f).has_value();}

    bool Find(const K& k) { return m.Find(Entry::make_key(k), true_f).has_value(); }
    bool Insert(const K& key) 
    {
      auto k = Entry::make_key(key);
      return !m.Insert(k, [&] {return entries_.make_entry(k, key);}, true_f).has_value();
    }

    bool Remove(const K& k)
    { return m.Remove(Entry::make_key(k), true_f).has_value(); }

    iterator find(const K& k) { return m.find(k); }

    std::pair<iterator,bool> insert(const value_type& entry) {
      return m.insert(entries_.make_entry(make_key(entry.first), entry)); }

    iterator erase(iterator pos) { return m.erase(pos); }
    size_t erase(const K& k) { return m.erase(k); }

  };
  
  template <typename K, class Hash = std::hash<K>, class KeyEqual = std::equal_to<K>>
  using parlay_unordered_set = std::conditional_t<std::is_trivially_copyable_v<K>,
						  unordered_set_<DirectEntries<SetData<K, Hash, KeyEqual>>>,
						  unordered_set_<IndirectEntries<SetData<K, Hash, KeyEqual>>>>;
}  // namespace parlay
#endif  // PARLAY_BIGATOMIC_HASH_LIST
