import unittest

from app.shared.vlan import interface_vlan_representation, merge_interface_vlan_tags, vlan_representation_tokens


class VlanRepresentationTests(unittest.TestCase):
    def test_lists_small_vlan_sets_individually(self) -> None:
        self.assertEqual(vlan_representation_tokens([1, 2, 3, 10]), ["1", "2", "3", "10"])

    def test_compacts_dense_large_ranges(self) -> None:
        self.assertEqual(vlan_representation_tokens(list(range(1, 101))), ["1-100"])

    def test_summarizes_sparse_large_sets_without_enumerating_every_vlan(self) -> None:
        vlans = list(range(1, 21)) + [100, 200, 300]
        self.assertEqual(vlan_representation_tokens(vlans), ["1-20", "100", "200", "300"])

    def test_reads_vlan_tokens_from_netbox_tags(self) -> None:
        tags = [{"slug": "vlan-1-20"}, {"slug": "custom-tag"}, {"slug": "vlan-300"}]
        self.assertEqual(interface_vlan_representation(tags), ["1-20", "300"])

    def test_merges_vlan_tags_without_losing_non_vlan_tags(self) -> None:
        tags = [{"id": 5, "slug": "core"}, {"id": 7, "slug": "vlan-1-20"}]
        self.assertEqual(merge_interface_vlan_tags(tags, [9, 10, 9]), [5, 9, 10])
