import unittest

from open_r1.rewards import (
    accuracy_reward,
    format_reward,
    get_cosine_scaled_reward,
    get_repetition_penalty_reward,
    reasoning_steps_reward,
)


class TestRewards(unittest.TestCase):
    def test_accuracy_reward_correct_answer(self):
        """Test accuracy_reward with a correct answer."""
        completion = [[{"content": r"\boxed{\frac{63}{400}}"}]]
        solution = [r"\frac{63}{400}"]

        rewards = accuracy_reward(completion, solution)
        self.assertEqual(rewards[0], 1.0)

    def test_accuracy_reward_wrong_answer(self):
        """Test accuracy_reward with an incorrect answer."""
        completion = [[{"content": r"\boxed{\frac{64}{400}}"}]]
        solution = [r"\frac{63}{400}"]

        rewards = accuracy_reward(completion, solution)
        self.assertEqual(rewards[0], 0.0)

    def test_format_reward_correct(self):
        """Test format_reward with correct format."""
        completion = [[{"content": "<think>Some reasoning</think><answer>The answer</answer>"}]]
        rewards = format_reward(completion)
        self.assertEqual(rewards[0], 1.0)

    def test_format_reward_incorrect(self):
        """Test format_reward with incorrect format."""
        incorrect_formats = [
            "<think>Only thinking</think>",
            "<answer>Only answer</answer>",
            "No tags at all",
            "<think>Missing closing</think><answer>Missing closing",
            "<think>Wrong order</answer><answer>Wrong order</think>",
        ]

        for fmt in incorrect_formats:
            completion = [[{"content": fmt}]]
            rewards = format_reward(completion)
            self.assertEqual(rewards[0], 0.0)

    def test_reasoning_steps_reward(self):
        """Test reasoning_steps_reward with various formats."""
        test_cases = [
            # Full credit cases (3 or more steps)
            ("Step 1: First step\nStep 2: Second step\nStep 3: Third step", 1.0),
            ("First, we do this.\nSecond, we do that.\nFinally, we conclude.", 1.0),
            # Partial credit cases (less than 3 steps)
            ("Step 1: Only step", 1 / 3),
            ("First, we do this.\nFinally, we conclude.", 2 / 3),
            # No credit case
            ("Just plain text without any clear steps", 0.0),
        ]

        for content, expected_reward in test_cases:
            completion = [[{"content": content}]]
            rewards = reasoning_steps_reward(completion)
            self.assertAlmostEqual(rewards[0], expected_reward)

    def test_multiple_completions(self):
        """Test handling multiple completions at once."""
        completions = [[{"content": r"\boxed{\frac{63}{400}}"}], [{"content": r"\boxed{\frac{64}{400}}"}]]
        solutions = [r"\frac{63}{400}", r"\frac{63}{400}"]

        rewards = accuracy_reward(completions, solutions)
        self.assertEqual(len(rewards), 2)
        self.assertEqual(rewards[0], 1.0)
        self.assertEqual(rewards[1], 0.0)

    def test_cosine_scaled_reward(self):
        """Test cosine_scaled_reward with various cases."""
        # Test parameters
        test_params = {
            "min_value_wrong": -1.0,
            "max_value_wrong": -0.5,
            "min_value_correct": 0.5,
            "max_value_correct": 1.0,
            "max_len": 100,
        }

        test_cases = [
            # Correct answers with different lengths
            (r"\boxed{\frac{63}{400}}", r"\frac{63}{400}", 20, 0.943),  # Short correct answer
            (r"\boxed{\frac{63}{400}}", r"\frac{63}{400}", 80, 0.547),  # Long correct answer
            # Wrong answers with different lengths
            (r"\boxed{\frac{64}{400}}", r"\frac{63}{400}", 20, -0.942),  # Short wrong answer
            (r"\boxed{\frac{64}{400}}", r"\frac{63}{400}", 80, -0.547),  # Long wrong answer
        ]

        for content, solution, content_len, expected_reward in test_cases:
            # Pad content to desired length
            padded_content = content + " " * (content_len - len(content))
            completion = [[{"content": padded_content}]]

            rewards = get_cosine_scaled_reward(**test_params)(completion, [solution])
            self.assertAlmostEqual(rewards[0], expected_reward, places=2)

    def test_format_reward_specific_multiline(self):
        """Test format_reward with a specific multiline input."""
        inputs = "<think>\nI will count each distinct object in the image:\n1. Purple scooter\n2. Red bicycle\n3. Green motorcycle\n4. Gray sedan\n5. Yellow school bus\n6. Small green double-decker bus\n7. Small red car\n8. Small purple car\n9. Small gray dirt bike\n\nThere are 9 distinct objects in total.\n</think>\n<answer>9</answer>"
        completion = [[{"content": inputs}]]
        rewards = format_reward(completion)
        self.assertEqual(rewards[0], 1.0)


class TestRepetitionPenaltyReward(unittest.TestCase):
    def test_positive_max_penalty_raises_value_error(self):
        with self.assertRaises(ValueError):
            get_repetition_penalty_reward(ngram_size=2, max_penalty=1.0)
        with self.assertRaisesRegex(ValueError, "max_penalty 1.5 should not be positive"):
            get_repetition_penalty_reward(ngram_size=2, max_penalty=1.5)

    def test_no_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=2, max_penalty=-1.0)
        completions = [[{"content": "this is a test sentence"}]]
        rewards = reward_fn(completions)
        self.assertEqual(rewards, [0.0])

    def test_full_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=2, max_penalty=-1.0)
        completions = [[{"content": "this this this this this"}]]

        rewards = reward_fn(completions)
        # (1 - 1/4) * -1 = -0.75
        self.assertEqual(rewards, [-0.8])

    def test_partial_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=2, max_penalty=-1.0)
        completions = [[{"content": "this is a this is a test"}]]

        rewards = reward_fn(completions)
        # Unique 2-grams: (this, is), (is, a), (a, this), (a, test).  4 unique out of 6 total
        # (1 - 4/6) * -1 = -1/3 = -0.3333...
        self.assertAlmostEqual(rewards[0], -0.4285714, places=4)

    def test_multiple_completions(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-0.5)
        completions = [
            [{"content": "this is a test"}],
            [{"content": "test test test test"}],
        ]

        rewards = reward_fn(completions)
        # Completion 1:  (this, is, a), (is, a, test) -> 2 unique / 2 total -> (1 - 2/2) * -0.5 = 0
        # Completion 2: (test, test, test) -> 1 unique / 2 total -> (1 - 1/2) * -0.5 = -0.25
        self.assertAlmostEqual(rewards[0], 0.0)
        self.assertAlmostEqual(rewards[1], -0.375)

    def test_empty_completion(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=2, max_penalty=-1.0)
        completions = [[{"content": ""}]]
        rewards = reward_fn(completions)
        self.assertEqual(rewards, [0.0])

    def test_different_ngram_size(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-2.0)
        completions = [[{"content": "this is a this is a test"}]]

        rewards = reward_fn(completions)
        self.assertAlmostEqual(rewards[0],  -0.8571428, places=4)

    def test_mixed_case(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=2, max_penalty=-1.0)
        completions = [
            [{"content": "This is A Test"}],
            [{"content": "this IS a test"}],
        ]

        rewards = reward_fn(completions)
        # both completions should produce the same reward, because the text gets lowercased
        self.assertAlmostEqual(rewards[0], rewards[1])

    def test_one_word_completion(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "word"}]]

        rewards = reward_fn(completions)
        self.assertEqual(rewards, [0.0])

    def test_two_word_completion(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "two words"}]]

        rewards = reward_fn(completions)
        self.assertEqual(rewards, [0.0])

    def test_three_word_completion(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "three different words"}]]

        rewards = reward_fn(completions)
        self.assertEqual(rewards, [0.0])

    def test_three_word_repetition_completion(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "word word word word"}]]

        rewards = reward_fn(completions)
        self.assertEqual(rewards, [-0.75])

    def test_four_word_completion_with_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "one two one two"}]]

        rewards = reward_fn(completions)
        # ngrams are (one two one) (two one two). unique is 2 and count is 2, therefore (1-1) * -1.
        self.assertEqual(rewards, [0.0])

    def test_five_word_completion_with_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-0.5)
        completions = [[{"content": "A B C A B"}]]

        rewards = reward_fn(completions)
        # (A B C) (B C A) (C A B). unique is 3. count is 3 (1-1) * -.5 = 0
        self.assertEqual(rewards, [0.0])

    def test_six_word_completion_with_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "A B C A B C"}]]

        rewards = reward_fn(completions)
        self.assertEqual(rewards, [-0.5])

    def test_long_completion_with_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "A B C A B C E F G A B C A B C"}]]
        rewards = reward_fn(completions)
        self.assertAlmostEqual(rewards[0], -0.6)

    def test_long_completion_without_repetition(self):
        reward_fn = get_repetition_penalty_reward(ngram_size=3, max_penalty=-1.0)
        completions = [[{"content": "A B C D E F G H I J K L"}]]

        rewards = reward_fn(completions)
        self.assertEqual(rewards, [0.0])


if __name__ == "__main__":
    unittest.main()
