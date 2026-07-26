import unittest
from cme_group_fix_api_for_futures import CmeFixSessionEngine, CmeFixOrderParams

class TestCmeFixSessionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CmeFixSessionEngine(sender_comp_id="FIRM_ALGO_01", target_comp_id="CME")

    def test_new_order_single_serialization(self):
        params = CmeFixOrderParams(
            symbol="ESZ5",
            side="1", # Buy
            quantity=10,
            price=5050.25,
            cl_ord_id="CL_12345",
            operator_id="OP_99",
            smp_id="SMP_888",
            smp_instruction="O",
            is_automated=True
        )
        
        fix_str = self.engine.create_new_order_single(params)
        
        # Verify headers & tags
        self.assertIn("35=D|", fix_str)
        self.assertIn("34=1|", fix_str)
        self.assertIn("49=FIRM_ALGO_01|", fix_str)
        self.assertIn("50=OP_99|", fix_str)
        self.assertIn("1028=N|", fix_str) # Automated
        self.assertIn("7928=SMP_888|", fix_str) # SMP ID
        self.assertIn("8000=O|", fix_str) # SMP Instruction
        self.assertIn("55=ESZ5|", fix_str)
        self.assertIn("44=5050.2500|", fix_str)

    def test_sequence_number_increment(self):
        params = CmeFixOrderParams(
            symbol="ESZ5", side="1", quantity=1, price=5000.0,
            cl_ord_id="1", operator_id="OP1", smp_id="SMP1"
        )
        msg1 = self.engine.create_new_order_single(params)
        msg2 = self.engine.create_new_order_single(params)
        
        self.assertIn("34=1|", msg1)
        self.assertIn("34=2|", msg2)
        self.assertEqual(self.engine.outbound_seq_num, 3)

    def test_sequence_gap_detection(self):
        # Inbound expects 1
        # Receive sequence 5 -> should trigger ResendRequest for 1 to 4
        inbound_msg = {35: "8", 34: "5"}
        resend_msg = self.engine.process_inbound_message(inbound_msg)
        
        self.assertIsNotNone(resend_msg)
        self.assertIn("35=2|", resend_msg)
        self.assertIn("7=1|", resend_msg)
        self.assertIn("16=4|", resend_msg)

    def test_valid_inbound_sequence(self):
        # Inbound expects 1
        inbound_msg = {35: "8", 34: "1"}
        resend_msg = self.engine.process_inbound_message(inbound_msg)
        
        self.assertIsNone(resend_msg)
        self.assertEqual(self.engine.expected_inbound_seq_num, 2)

if __name__ == '__main__':
    unittest.main()
