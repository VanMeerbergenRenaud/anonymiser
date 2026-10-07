"""
first_names — Prénoms courants en Belgique et en France.

Second détecteur de personnes, indépendant du NER : le modèle laisse parfois
passer un nom lorsque le contexte de la phrase est pauvre (« … balies » et
Alex Tallon et par… »). Un prénom connu suivi d'un nom (« Alex Tallon »,
« Karim BOUZIANE ») est reconnu comme une personne, de même qu'un prénom
seul en milieu de phrase (« mon ami Yannick »).

La liste couvre les prénoms les plus portés en Belgique (francophones,
néerlandophones, germanophones) et dans les communautés d'origine
immigrée (Maghreb, Turquie, Afrique centrale, Europe du Sud et de l'Est).
Formes « fold » (capitales sans accents).
"""

from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Majuscules sans accents (même forme que ``api.recognizers.fold``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()

_NAMES = """
Aaron Abdel Abdelaziz Abdelali Abdelhakim Abdelkader Abdellah Abderrahim Abdeslam Abdou
Abdoulaye Achille Achraf Adam Adel Adnan Adrian Adrien Ahmed Ahmet Aimé Alain Alan Albert
Alberto Aldo Alessandro Alex Alexander Alexandre Alexis Alfons Alfred Ali Alphonse Amadou
Amaury Ambroise Amine Anas André Andrea Andreas Andrei Andrés Andrzej Angelo Anthony Antoine
Anton Antonio Armand Arnaud Arno Arthur Augustin Aurélien Axel Ayoub Aziz Badr Baptiste Bart
Basile Bastien Baudouin Bekir Ben Benjamin Benoît Benoit Bernard Bert Bertrand Bilal Bjorn
Björn Boris Brahim Bram Brecht Brice Bruno Burak Carl Carlo Carlos Cédric Cem Charles Charlie
Christian Christophe Christopher Claude Clément Corentin Cristian Cyril Cyrille Damien Daniel
Dany Dario David Davy Denis Didier Diego Dimitri Dirk Djamel Dominique Driss Dylan Édouard
Edouard Eddy Edgar Elias Eliott Emile Émile Emmanuel Emre Enzo Eric Éric Erik Ernest Esteban
Étienne Etienne Eugène Fabian Fabien Fabio Fabrice Farid Fatih Félix Ferdinand Fernand
Fernando Filip Filippo Florent Florian Florin Francesco Francis Franck Francisco François
Frank Frans Frédéric Frederik Gabriel Gaëtan Gaël Gaston Gauthier Geert Geoffrey Georges
Gérald Gérard Gert Ghislain Gianni Gilbert Gilles Giorgio Giovanni Giuseppe Glenn Grégoire
Grégory Gregory Guido Guillaume Gustave Guy Hakan Hamid Hamza Hans Hassan Hendrik Henri Henry
Herman Hervé Hicham Hubert Hugo Hugues Hüseyin Ibrahim Ignace Ilias Ilyas Imad Ioan Ionut
Ismaël Ismail Ivan Jacques Jakub Jamal James Jan Jasper Javier Jean Jef Jens Jérémie Jérémy
Jeroen Jérôme Joachim Joao João Joël Joeri Johan Johann Johannes John Jonas Jonathan Joost
Jordan Jordi Joris José Joseph Joshua Jozef Juan Jules Julien Justin Kamel Karel Karim Karl
Kemal Kenneth Kevin Kévin Khalid Kim Koen Krzysztof Kurt Lars Laurent Lennert Léo Léon
Léonard Liam Lionel Loïc Lorenzo Louis Luc Luca Lucas Lucien Ludo Ludovic Luigi Luis Lukas
Maarten Mamadou Manuel Marc Marcel Marco Marek Mario Marius Mark Martijn Martin Massimo
Mathias Mathieu Mathis Matteo Matthias Matthieu Maurice Max Maxence Maxime Mehdi Mehmet
Michaël Michael Michel Michiel Miguel Mihai Mike Milan Mohamed Mohammed Mounir Murat
Mustafa Mustapha Nabil Nathan Nicolas Niels Nico Noah Noé Nordine Olivier Omar Oscar Oussama
Pablo Pascal Patrice Patrick Paul Paulo Paweł Pawel Pedro Peter Philippe Piet Pieter Piotr
Quentin Rachid Rafael Raphaël Raymond Recep Redouane Rémi Rémy Renaud René Ricardo Richard
Rik Robert Roberto Robin Rodolphe Roger Roland Romain Roméo Ronald Ruben Rudi Rudy Rui Ryan
Saïd Said Salim Salvatore Sami Samir Samuel Sander Sébastien Selim Serge Sergio Simon Sofiane
Soufiane Stefan Stefano Stéphane Steve Steven Stijn Sven Sylvain Tarik Théo Théodore
Thibault Thibaut Thierry Thomas Tim Timothée Tom Tomasz Tommy Toon Tristan Valentin Valère
Victor Vincent Walter Wim Willem William Wouter Xavier Yann Yannick Yasin Yassine Yohan
Younes Youssef Yusuf Yves Zakaria

Adèle Adeline Agathe Agnès Agnieszka Aïcha Aicha Alexandra Alice Alicia Aline Alison
Amandine Amélie Amina Amira An Ana Anaïs Andrée Angela Angélique Anja Anke Ann Anna Anne
Annelies Annick Annie Antoinette Antonella Ariane Audrey Aurélie Axelle Ayşe Ayse Barbara
Béatrice Bénédicte Bernadette Bieke Brigitte Carine Carla Carmen Caroline Catherine Cécile
Céline Chantal Charline Charlotte Chloé Chloë Christel Christelle Christiane Christine
Cindy Claire Clara Claudia Claudine Clémence Colette Coralie Corinne Cristina Cynthia
Danielle Delphine Denise Diane Dorothée Édith Edith Elena Eléonore Elif Éliane Eliane Élisa
Elisa Élisabeth Elisabeth Élise Elise Ella Ellen Eline Élodie Elodie Eloïse Els Émilie
Emilie Emma Estelle Eva Éva Evelyne Fabienne Fanny Fatiha Fatima Fatma Femke Fien Francesca
Françoise Gabrielle Geneviève Ghislaine Gisèle Giulia Griet Hafsa Hajar Hanna Hannah Hatice
Hélène Hilde Houda Ikram Ilse Imane Inès Ines Inge Ingrid Ioana Iris Isabelle Jacqueline
Jana Jeanne Jeannine Jennifer Jessica Joëlle Joke Josée Josiane Julia Julie Juliette Justine
Karima Karin Katarzyna Katia Katrien Khadija Kristel Laetitia Latifa Laura Laure Laurence
Léa Leila Leïla Léna Leyla Lien Liesbeth Liliane Lina Linda Lisa Lise Lore Lotte Loubna
Louise Lucia Lucie Lydia Madeleine Maëlle Magdalena Malika Manon Margaux Margot Marguerite
Maria Marianne Marie Marijke Marine Marion Marleen Marthe Martine Maryse Mathilde Maud
Mélanie Mélissa Meriem Meryem Michèle Micheline Mireille Monika Monique Mounia Muriel
Myriam Nadia Nadine Naïma Naima Nathalie Nawal Nele Nina Noémie Noor Nora Océane Odette
Odile Pascale Patricia Paula Pauline Perrine Rachel Rania Rebecca Renée Rita Sabine Sabrina
Salma Samia Samira Sandra Sandrine Sara Sarah Saskia Séverine Siham Silvia Simone Sofia
Sofie Solange Sonia Sophie Souad Stéphanie Suzanne Sylviane Sylvie Tatiana Thérèse Tine
Valérie Vanessa Veerle Véronique Virginie Viviane Wafa Wendy Yasmina Yasmine Yousra Yvette
Yvonne Zeynep Zineb Zoé Zohra
"""

FIRST_NAMES: frozenset[str] = frozenset(fold(n) for n in _NAMES.split())
"""Prénoms (forme « fold »)."""

AMBIGUOUS_FIRST_NAMES: frozenset[str] = frozenset(fold(n) for n in """
Claude Dominique Camille Marine Victoria Florence Nancy Valence Lourdes Rose Pierre Ange
Aurore Constance Prudence Blanche Olive Mai Avril France Jordan Paris Orange Kim Ben An Ann
Ana Iris Max Tom Mark Glenn Lore Jana Noor Nora Ella Mike Milan Gaston Martin Laurent Thomas
Simon Bernard Richard Robert Vincent Michel Henri Henry Louis Léon Lambert Jacques Nicolas
Christian Denis Gilles Benoît Benoit Marc Paul Jean Luc Guy Didier Roger Albert Gérard
""".split())
"""Prénoms également courants comme nom de famille, nom de ville, mot ou
mois : pas retenus seuls (« Cour d'appel de Nancy », « le juge Martin »)
— seulement suivis d'un nom."""


def is_first_name(token: str) -> bool:
    """« Jean », « Jean-Marc », « Marie-Claire » (toutes les parties connues)."""
    parts = [p for p in token.split("-") if p]
    return bool(parts) and all(fold(p) in FIRST_NAMES for p in parts)
