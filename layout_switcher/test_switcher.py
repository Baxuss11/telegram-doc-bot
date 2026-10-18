"""Тесты: python -m unittest test_switcher.py  (pynput для них не нужен)."""
import unittest

from detector import EN, RU, Detector, convert
from switcher import Engine, Replacement

DETECTOR = Detector(exceptions=set())


class ConvertTest(unittest.TestCase):
    def test_both_directions(self):
        self.assertEqual(convert('ghbdtn', EN), 'привет')
        self.assertEqual(convert('руддщ', RU), 'hello')

    def test_punctuation_keys_are_letters(self):
        self.assertEqual(convert('[jhjij', EN), 'хорошо')
        self.assertEqual(convert('ü', EN), 'ü')  # чужие символы не трогаем

    def test_case_is_kept(self):
        self.assertEqual(convert('Ghbdtn', EN), 'Привет')
        self.assertEqual(convert('ЗКШМУЕ', RU), 'PRIVET')


class DetectorTest(unittest.TestCase):
    def assertFixes(self, typed, expected):
        self.assertEqual(DETECTOR.fix(typed), expected, typed)

    def test_russian_typed_in_english_layout(self):
        for typed, expected in [('ghbdtn', 'привет'), ('rfr', 'как'), ('ltkf', 'дела'),
                                ('yt', 'не'), ('[jhjij', 'хорошо'), ('j,sxyj', 'обычно'),
                                (';t', 'же'), ('ntkf[', 'телах')]:
            self.assertFixes(typed, expected)

    def test_english_typed_in_russian_layout(self):
        for typed, expected in [('руддщ', 'hello'), ('цщкдв', 'world'), ('ыгзук', 'super'),
                                ('вщтэе', "don't")]:
            self.assertFixes(typed, expected)

    def test_correct_words_are_left_alone(self):
        for word in ['привет', 'hello', 'рук', 'her', 'it.', 'ещё', 'Telegram', 'a', 'и']:
            self.assertFixes(word, None)

    def test_unknown_word_by_letter_statistics(self):
        # «сочинениях» нет в словаре, но буквосочетания явно русские
        self.assertFixes(convert('сочинениях', RU), 'сочинениях')
        self.assertFixes('сочинениях', None)

    def test_punctuation_and_case(self):
        self.assertFixes('Ghbdtn!', 'Привет!')
        self.assertFixes('ghbdtn,', 'привет,')
        self.assertFixes('"ghbdtn"', '"привет"')
        self.assertFixes('GHBDTN', 'ПРИВЕТ')

    def test_skips_codes_and_mixed_text(self):
        for word in ['abc123', 'ghbвет', 'gHBDTN', 'user@mail.ru', '']:
            self.assertFixes(word, None)

    def test_exceptions(self):
        detector = Detector(models=DETECTOR.models, exceptions={'ghbdtn'})
        self.assertIsNone(detector.fix('ghbdtn'))

    def test_single_letter_before_fixed_word(self):
        self.assertEqual(DETECTOR.fix_previous('z', EN), 'я')
        self.assertEqual(DETECTOR.fix_previous('b', EN), 'и')
        self.assertIsNone(DETECTOR.fix_previous('a', EN))   # английское «a» чаще русского «ф»
        self.assertIsNone(DETECTOR.fix_previous('я', EN))   # уже русская буква


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(Detector(models=DETECTOR.models, exceptions=set()))

    def test_replaces_word_and_space(self):
        self.assertEqual(self.engine.on_word('ghbdtn', adjacent=False),
                         Replacement(7, 'привет ', RU))

    def test_correct_word_needs_nothing(self):
        self.assertIsNone(self.engine.on_word('hello', adjacent=False))

    def test_previous_single_letter_is_fixed_too(self):
        self.assertIsNone(self.engine.on_word('z', adjacent=False))
        self.assertEqual(self.engine.on_word('ljvf', adjacent=True), Replacement(7, 'я дома ', RU))

    def test_previous_letter_ignored_after_cursor_moved(self):
        self.engine.on_word('z', adjacent=False)
        self.assertEqual(self.engine.on_word('ljvf', adjacent=False), Replacement(5, 'дома ', RU))

    def test_pause_undoes_and_remembers(self):
        self.engine.on_word('ghbdtn', adjacent=False)
        self.assertEqual(self.engine.on_manual(''), Replacement(7, 'ghbdtn ', EN))
        self.assertIsNone(self.engine.on_word('ghbdtn', adjacent=False))

    def test_pause_converts_unfinished_word(self):
        self.assertEqual(self.engine.on_manual('ghb'), Replacement(3, 'при', RU))

    def test_pause_converts_last_word_when_auto_missed_it(self):
        # 'her' — нормальное английское слово, но хотели написать «рук»
        self.assertIsNone(self.engine.on_word('her', adjacent=False))
        self.assertEqual(self.engine.on_manual(''), Replacement(4, 'рук ', RU))

    def test_unknown_word_converted_by_letter_statistics(self):
        self.assertEqual(self.engine.on_word('Ntktuhfv', adjacent=False),  # «Телеграм»
                         Replacement(9, 'Телеграм ', RU))

    def test_manual_word_is_not_checked_again(self):
        self.assertIsNone(self.engine.on_word('ghbdtn', adjacent=False, check=False))

    def test_disabled(self):
        self.engine.toggle()
        self.assertIsNone(self.engine.on_word('ghbdtn', adjacent=False))


if __name__ == '__main__':
    unittest.main()
